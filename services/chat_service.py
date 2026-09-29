"""Chatbot service — Gemini agent loop over the shop's database with streaming.

Flow per question:
  1. Classify intent: GREETING / IRRELEVANT / RELEVANT (context-aware)
  2. Greeting → stream a friendly reply
  3. Irrelevant → stream "not my business" message
  4. Relevant → run agent loop (tools: customer_summary, query_database),
     streaming the answer token-by-token via SSE.

WHY RAW REST INSTEAD OF THE google-genai SDK:
The only google-genai release compatible with this project's pinned
pydantic==2.7.1 is 1.2.0, which predates Gemini 3 and silently drops the
`thoughtSignature` field. Gemini 3 REJECTS a tool round-trip whose functionCall
parts are missing that signature (400 INVALID_ARGUMENT). Talking to the REST API
directly lets us echo the model's parts back verbatim — signatures intact — with
zero dependency changes to a production app.

Everything is logged via utils/chat_logger → backend/logs/chat.log.
"""
import os
import json
import re
import asyncio
from datetime import date
from typing import Optional

import httpx
from fastapi import Request

from repo import chat_repo
from utils.sql_guard import validate_select_only, SQLGuardError
from utils.chat_logger import chat_logger, trunc

API_ROOT = "https://generativelanguage.googleapis.com/v1beta/models"
MODEL = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")
# Cheap, fast model for the single-word intent classification call.
CLASSIFICATION_MODEL = os.getenv("GEMINI_CLASSIFY_MODEL", "gemini-3.5-flash-lite")
MAX_TOOL_ROUNDS = 6
MAX_HISTORY_MESSAGES = 12
REQUEST_TIMEOUT = 120

_client: httpx.AsyncClient | None = None


class ChatNotConfiguredError(Exception):
    pass


class ChatServiceError(Exception):
    """User-safe chatbot failure message."""


def _get_key() -> str:
    key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not key:
        raise ChatNotConfiguredError(
            "The chatbot is not configured yet — add GEMINI_API_KEY to backend/.env and restart."
        )
    return key


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(timeout=REQUEST_TIMEOUT)
    return _client


def _http_message(status: int, detail: str) -> str:
    """Map an HTTP failure to a short, user-safe message (and log the detail)."""
    snippet = trunc(detail, 250)
    if status in (401, 403):
        chat_logger.error("Gemini auth failed (%s): %s", status, snippet)
        return "The AI key is invalid or lacks access. Check GEMINI_API_KEY in backend/.env."
    if status == 429:
        chat_logger.warning("Gemini quota/rate limited: %s", snippet)
        return "The AI is rate-limited or out of quota right now — please try again in a minute."
    if status == 404:
        chat_logger.error("Gemini model not found (%s): %s", MODEL, snippet)
        return f"The AI model '{MODEL}' isn't available for this API key."
    chat_logger.error("Gemini API error %s: %s", status, snippet)
    return "The AI service returned an error — try again shortly."


# ── Intent classification ─────────────────────────────────────────────────────

_GREETING_PATTERNS = re.compile(
    r"^\s*(hi|hello|hey|yo|sup|good\s*(morning|afternoon|evening|night)"
    r"|how\s*(are|r)\s*(you|u)|how'?s?\s*(it going|things)|what'?s\s*up|namaste|vanakkam"
    r"|thank(s|you)|bye|goodbye|see\s*you|take\s*care"
    r"|good\s*evening|good\s*morning|good\s*afternoon"
    r"|hii+|helo|hllo|heyy+)\s*[!.?]*\s*$",
    re.IGNORECASE,
)

INTENT_PROMPT = """You are given the recent conversation and the LATEST user
message. Classify ONLY the latest user message into EXACTLY ONE category, using
the conversation for context.

GREETING — Greetings, pleasantries, thank-you, bye, small talk with no data request.
  Examples: "hello", "hi", "good morning", "thank you", "bye", "how are you"

RELEVANT — Anything about the pawn shop register: bills, customers, loans,
  money owed, interest, gold, silver, weights, purchases, dates, serial numbers,
  employee queries, reports, or any business data. This ALSO includes short
  follow-ups that continue an ongoing data conversation — "yes", "ok", "sure",
  "show it", "that one", "the first", "the second one", "more", "and silver?" —
  treat these as RELEVANT continuations of the previous topic.
  Examples: "how much does Ravi owe?", "show me active silver bills", "yes", "the second one"

IRRELEVANT — Something completely unrelated to the pawn shop business:
  politics, sports, cooking, coding, math puzzles, general knowledge, etc.

Reply with ONLY the single word: GREETING, RELEVANT, or IRRELEVANT."""


async def classify_intent(question: str, history: list[dict] | None = None) -> str:
    """Classify user intent, using recent conversation context so follow-ups
    ('yes', 'show it', 'the second one') are understood as continuations rather
    than mistaken for standalone greetings."""
    q = question.strip()
    history = history or []

    # Fast path only for the very FIRST message — a bare "hi" with no prior
    # context. Mid-conversation, a short "yes"/"ok" is a follow-up, not a
    # greeting, so we let the context-aware classifier decide.
    if not history and _GREETING_PATTERNS.match(q):
        return "GREETING"

    try:
        recent = [
            m for m in history[-4:]
            if m.get("role") in ("user", "assistant") and m.get("content")
        ]
        if recent:
            ctx = "\n".join(
                f"{'Assistant' if m['role'] == 'assistant' else 'User'}: {str(m['content'])[:500]}"
                for m in recent
            )
            content = f"Recent conversation:\n{ctx}\n\nLatest user message to classify: {q}"
        else:
            content = q

        client = _get_client()
        resp = await client.post(
            f"{API_ROOT}/{CLASSIFICATION_MODEL}:generateContent",
            json={
                "systemInstruction": {"parts": [{"text": INTENT_PROMPT}]},
                "contents": [{"role": "user", "parts": [{"text": content}]}],
            },
            headers={"x-goog-api-key": _get_key(), "Content-Type": "application/json"},
        )
        if resp.status_code != 200:
            raise RuntimeError(f"HTTP {resp.status_code}: {trunc(resp.text, 150)}")

        data = resp.json()
        parts = data["candidates"][0]["content"]["parts"]
        text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
        upper = text.strip().upper()
        for label in ("GREETING", "RELEVANT", "IRRELEVANT"):
            if label in upper:
                chat_logger.info("intent classified=%s question=%s history=%d", label, trunc(q, 200), len(history))
                return label
        return "RELEVANT"
    except Exception as e:
        chat_logger.warning("intent classification failed, defaulting RELEVANT: %s", e)
        return "RELEVANT"


def _greeting_response(question: str) -> str:
    """Return a context-aware greeting."""
    q = question.lower().strip()
    if any(w in q for w in ("how are you", "how r u", "how are u", "how r you", "how you doing", "how's it going")):
        return "I'm doing great, thanks for asking! I'm here and ready to help with your pawn bills, customers, loans, and purchases. What would you like to know?"
    if any(w in q for w in ("good morning",)):
        return "Good morning! Welcome to Guptha's. How can I help you with your register today?"
    if any(w in q for w in ("good evening",)):
        return "Good evening! Welcome to Guptha's. What would you like to know about your bills or customers?"
    if any(w in q for w in ("good afternoon",)):
        return "Good afternoon! Welcome to Guptha's. Ask me anything about your pawn bills, customers, or purchases."
    if any(w in q for w in ("thank", "thanks")):
        return "You're welcome! Let me know if you need anything else about the register."
    if any(w in q for w in ("bye", "goodbye", "see you", "take care")):
        return "Goodbye! Have a great day. I'm here whenever you need help with the register."
    if any(w in q for w in ("what's up", "whats up", "sup")):
        return "All good here! Ready to help you with the register. Anything you'd like to check on your bills or customers?"
    return "Hello! Welcome to Guptha's. I can help you with bills, customers, loans, interest, and purchases. What would you like to know?"


# ── System prompt & tools ─────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are the assistant for Guptha's, a gold & silver pawn shop register.
You answer questions using ONLY the shop's database, via the tools provided. Today is {today}.

DATABASE SCHEMA (PostgreSQL — all queries MUST be read-only SELECT):

pawns — one row per pawn bill (loan against gold/silver collateral)
  id uuid, serial_no int, series varchar(2) NULL,   -- bill number is series+serial_no, e.g. A123 or plain 123
  entry_date date, borrower_name varchar, relative_name varchar ("Father: X" / "Spouse: X"),
  phone text (JSON array as text), aadhar varchar, address text,
  item_description text, item_weight numeric(grams),
  item_weight_gold numeric, item_weight_silver numeric,
  collateral_type enum('gold','silver','both'),
  loan_amount numeric, interest_rate numeric (% per month),
  loan_amount_gold numeric, interest_rate_gold numeric,
  loan_amount_silver numeric, interest_rate_silver numeric,
  is_released bool, released_date date, actual_release_amount numeric,
  is_sold bool, sold_date date, is_cancelled bool,
  renewed bool, renewed_from uuid, renewed_to uuid,
  created_by_username varchar, created_by_name varchar, created_at timestamp
  STATUS RULES: active = NOT is_released AND NOT is_cancelled;
  released = is_released AND NOT is_sold AND NOT is_cancelled;
  sold = is_sold AND NOT is_cancelled; cancelled = is_cancelled.

additional_amounts — "dhafa": extra loan later added to a bill
  id uuid, pawn_id uuid → pawns.id, amount numeric, date date, interest_rate numeric, note varchar
prepayments — partial repayments: id uuid, pawn_id uuid, amount numeric, date date, note varchar
interest_payments — interest-only payments: id uuid, pawn_id uuid, amount numeric, date date, note varchar

purchases — old-gold purchases from customers (no-objection forms)
  id uuid, serial_no int, series varchar NULL, purchase_date date,
  seller_name varchar, relative_name varchar, phone text, aadhar varchar, address text,
  item_description text, item_weight numeric, metal_type enum('gold','silver','both'),
  amount_paid numeric, notes text, created_by_username varchar, created_at timestamp

TOOL RULES:
- For ANY question about a customer's bills, totals, or money owed (payable/interest),
  ALWAYS call customer_summary first — it computes exact payable amounts with the
  shop's own interest formula (compound yearly + monthly, dhafa, prepayments). Do NOT
  attempt interest math in SQL; SQL cannot reproduce the formula.
- For everything else (counts, lists, date ranges, purchases, aggregates), call
  query_database with ONE plain SELECT statement. Use ILIKE '%…%' for name matching.
  Always LIMIT result sets to what you need (≤ 100 rows).
- Never modify data. Only SELECT is permitted; anything else is blocked.
- Call tools SILENTLY. Do not narrate ("let me check…", "looking that up…") before
  a tool call — emit no text until you have the tool results and are writing the
  final answer. This keeps the streamed reply clean.

ANSWER STYLE:
- Answer in short, plain language. Format money as ₹ with Indian digit grouping (₹1,23,456).
- When amounts involve gold and silver, show the split.
- Say "active" bills vs "released" clearly. If nothing matches, say so and suggest
  the closest matching names you found.
- Never invent data. If the tools return nothing, say the data isn't there.

FORMATTING RULES (critical — this is a chat bubble, not a terminal):
- NEVER use markdown tables (|---|---|), JSON, or code blocks.
- For listing bills, use this clean format per bill:

  Bill #A123 · Active · Gold
  Borrower: Raghava (S/O: Venkatesh)
  Date: 15/03/2025 · Loan: ₹50,000
  Payable today: ₹54,200

  Bill #A124 · Released · Silver
  Borrower: Sunita (W/O: Ramesh)
  Date: 01/06/2025 · Loan: ₹30,000
  Released: 01/09/2025 · Collected: ₹32,100

- For summary counts, use short bullet lines:
  • 3 active bills — total payable: ₹1,42,500
  • 2 released bills
  • 1 cancelled bill
- Keep answers concise. Show max 10 bills unless asked for more.
- Separate multiple bills with a blank line.
"""

# Gemini function declarations (OpenAPI-style schema).
TOOLS = [{
    "functionDeclarations": [
        {
            "name": "customer_summary",
            "description": (
                "Exact summary of a customer's pawn bills: count, status, principal, and "
                "payable-today amounts computed with the shop's exact interest formula "
                "(including dhafa, prepayments, interest payments). Searches BOTH "
                "borrower_name AND relative_name (S/O, D/O, W/O) case-insensitively. "
                "ALWAYS use this for money-owed / interest / 'how many bills does X have' "
                "questions, and when a user asks about someone by any name."
            ),
            "parameters": {
                "type": "OBJECT",
                "properties": {
                    "name": {"type": "STRING", "description": "Customer name or part of it"}
                },
                "required": ["name"],
            },
        },
        {
            "name": "query_database",
            "description": (
                "Run ONE read-only SELECT query against the shop database and get rows back. "
                "Use for counts, lists, filters, purchases, and aggregates that don't involve "
                "computing interest. INSERT/UPDATE/DELETE and every other statement type are "
                "blocked by guardrails."
            ),
            "parameters": {
                "type": "OBJECT",
                "properties": {
                    "sql": {"type": "STRING", "description": "A single SELECT statement"}
                },
                "required": ["sql"],
            },
        },
    ]
}]


# ── Tool execution ────────────────────────────────────────────────────────────

async def _run_tool(name: str, tool_input: dict, request: Optional[Request] = None) -> tuple[str, bool]:
    """Execute one tool call. Returns (result_json, is_error)."""
    if name == "customer_summary":
        cname = str(tool_input.get("name", "")).strip()
        if not cname:
            return json.dumps({"error": "name is required"}), True
        data = await chat_repo.customer_summary(cname, request)
        return json.dumps(data, ensure_ascii=False), False

    if name == "query_database":
        sql = str(tool_input.get("sql", ""))
        chat_logger.info("sql_attempt: %s", trunc(sql, 500))
        try:
            safe_sql = validate_select_only(sql)
        except SQLGuardError as e:
            chat_logger.warning("sql_BLOCKED: %s | query: %s", e, trunc(sql, 500))
            return json.dumps({"error": f"Query blocked by guardrails: {e}"}), True
        try:
            data = await chat_repo.run_readonly_query(safe_sql, request)
        except Exception as e:
            chat_logger.warning("sql_failed: %s | query: %s", trunc(str(e), 300), trunc(sql, 500))
            return json.dumps({"error": f"Query failed: {trunc(str(e), 300)}"}), True
        chat_logger.info("sql_ok rows=%d truncated=%s", data["row_count"], data["truncated"])
        return json.dumps(data, ensure_ascii=False), False

    return json.dumps({"error": f"Unknown tool {name}"}), True


def _history_to_contents(history: list[dict], question: str) -> list[dict]:
    """Build Gemini `contents` from prior conversation + the new question. Keeps
    the thread as context so follow-ups work, maps assistant→model, and
    guarantees the list starts with a user turn."""
    contents = []
    for m in (history or [])[-MAX_HISTORY_MESSAGES:]:
        role, text = m.get("role"), m.get("content")
        if role not in ("user", "assistant") or not text:
            continue
        contents.append({
            "role": "model" if role == "assistant" else "user",
            "parts": [{"text": str(text)[:4000]}],
        })
    while contents and contents[0]["role"] != "user":
        contents.pop(0)
    contents.append({"role": "user", "parts": [{"text": question}]})
    return contents


async def _stream_round(system: dict, contents: list, sink: dict):
    """Stream ONE model turn over SSE.

    Yields answer-text deltas only (never the model's internal `thought` parts).
    Collects every raw part verbatim into sink["parts"] — preserving
    `thoughtSignature`, which Gemini 3 requires when the tool result is sent
    back — and any function calls into sink["calls"].
    """
    client = _get_client()
    body = {"systemInstruction": system, "contents": contents, "tools": TOOLS}
    headers = {"x-goog-api-key": _get_key(), "Content-Type": "application/json"}

    async with client.stream(
        "POST", f"{API_ROOT}/{MODEL}:streamGenerateContent?alt=sse",
        json=body, headers=headers,
    ) as resp:
        if resp.status_code != 200:
            detail = (await resp.aread()).decode("utf-8", "replace")
            raise ChatServiceError(_http_message(resp.status_code, detail))

        async for line in resp.aiter_lines():
            if not line.startswith("data:"):
                continue
            raw = line[5:].strip()
            if not raw:
                continue
            try:
                chunk = json.loads(raw)
            except json.JSONDecodeError:
                continue
            for cand in chunk.get("candidates", []):
                for part in cand.get("content", {}).get("parts", []):
                    sink["parts"].append(part)          # verbatim → keeps thoughtSignature
                    fc = part.get("functionCall")
                    if fc:
                        sink["calls"].append(fc)
                    elif part.get("text") and not part.get("thought"):
                        yield part["text"]


# ── Streaming ask (SSE) ──────────────────────────────────────────────────────

def _chunk_text(text: str, size: int = 12) -> list[str]:
    """Split text into chunks for smooth streaming of canned replies.
    Tries to break at word boundaries for natural-looking output."""
    chunks = []
    i = 0
    while i < len(text):
        end = min(i + size, len(text))
        if end < len(text):
            space = text.rfind(" ", i + size // 2, end)
            if space > i:
                end = space + 1
        chunks.append(text[i:end])
        i = end
    return chunks


async def ask_stream(question: str, history: list[dict], username: str, request: Optional[Request] = None):
    """Async generator that yields SSE event dicts.

    Event types:
      {"type": "thinking", "content": "..."}  — shown while tools execute
      {"type": "text", "content": "..."}      — answer text chunks
      {"type": "reset"}                       — discard streamed preamble
      {"type": "done"}                        — stream finished
      {"type": "error", "content": "..."}     — error message
    """
    _get_key()  # raises ChatNotConfiguredError early if unset
    chat_logger.info("stream_question user=%s: %s", username, trunc(question, 500))

    # ── Step 1: classify intent (context-aware so follow-ups like "yes" work) ─
    try:
        intent = await classify_intent(question, history)
    except Exception as e:
        chat_logger.warning("intent classification failed: %s — defaulting RELEVANT", e)
        intent = "RELEVANT"

    # ── Greeting ───────────────────────────────────────────────────────────
    if intent == "GREETING":
        reply = _greeting_response(question)
        chat_logger.info("greeting user=%s reply=%s", username, trunc(reply, 200))
        for chunk in _chunk_text(reply, 20):
            yield {"type": "text", "content": chunk}
            await asyncio.sleep(0.015)
        yield {"type": "done"}
        return

    # ── Irrelevant ─────────────────────────────────────────────────────────
    if intent == "IRRELEVANT":
        reply = (
            "I'm sorry, I can only help with questions about Guptha's pawn shop register — "
            "bills, customers, loans, interest, and purchases. "
            "Please ask something related to the shop's data."
        )
        chat_logger.info("irrelevant user=%s", username)
        for chunk in _chunk_text(reply, 20):
            yield {"type": "text", "content": chunk}
            await asyncio.sleep(0.015)
        yield {"type": "done"}
        return

    # ── Relevant: agent loop with tools ────────────────────────────────────
    contents = _history_to_contents(history, question)
    system = {"parts": [{"text": SYSTEM_PROMPT.format(today=date.today().isoformat())}]}

    yield {"type": "thinking", "content": "Checking the register…"}

    try:
        for _round in range(MAX_TOOL_ROUNDS):
            sink = {"parts": [], "calls": []}
            streamed_len = 0

            async for delta in _stream_round(system, contents, sink):
                streamed_len += len(delta)
                yield {"type": "text", "content": delta}

            if not sink["calls"]:
                answer = "".join(
                    p.get("text", "") for p in sink["parts"] if not p.get("thought")
                ).strip()
                chat_logger.info(
                    "stream_answer user=%s rounds=%d len=%d", username, _round + 1, len(answer)
                )
                # Fallback: if the model returned no text at all, emit a stand-in
                # so the bubble is never left empty.
                if streamed_len == 0:
                    yield {"type": "text", "content": answer or "I couldn't find an answer for that."}
                yield {"type": "done"}
                return

            # This round ended in a tool call. Any text streamed above was just
            # preamble narration ("let me look that up…") — tell the client to
            # clear it so only the final answer stays in the bubble.
            if streamed_len > 0:
                yield {"type": "reset"}

            # Echo the model turn back VERBATIM (thoughtSignature intact), then
            # append the tool results.
            contents.append({"role": "model", "parts": sink["parts"]})
            responses = []
            for fc in sink["calls"]:
                name = fc.get("name", "")
                args = fc.get("args") or {}
                chat_logger.info("stream_tool_call %s input=%s", name, trunc(json.dumps(args, ensure_ascii=False), 500))
                yield {"type": "thinking", "content": f"Looking up {name.replace('_', ' ')}…"}
                result, _is_error = await _run_tool(name, args, request)
                responses.append({
                    "functionResponse": {"name": name, "response": {"result": result}}
                })
            contents.append({"role": "user", "parts": responses})

        # Max rounds exhausted
        chat_logger.warning("max tool rounds reached user=%s", username)
        yield {"type": "text", "content": "That question needed too many lookups — please ask it more specifically."}
        yield {"type": "done"}

    except ChatNotConfiguredError as e:
        yield {"type": "error", "content": str(e)}
        yield {"type": "done"}
    except ChatServiceError as e:
        yield {"type": "error", "content": str(e)}
        yield {"type": "done"}
    except httpx.TimeoutException:
        chat_logger.error("Gemini request timed out")
        yield {"type": "error", "content": "The AI took too long to respond — please try again."}
        yield {"type": "done"}
    except httpx.RequestError as e:
        chat_logger.error("Gemini connection failed: %s", trunc(str(e), 200))
        yield {"type": "error", "content": "Couldn't reach the AI service — check the internet connection."}
        yield {"type": "done"}
    except Exception as e:
        chat_logger.error("Unexpected error in ask_stream: %s", trunc(str(e), 500))
        yield {"type": "error", "content": "Something went wrong — please try again."}
        yield {"type": "done"}


# ── Non-streaming ask (backward compat for POST /api/chat/) ──────────────────

async def ask(question: str, history: list[dict], username: str, request: Optional[Request] = None) -> str:
    """Answer one question, collecting the streamed events into a single string.
    Shares the exact same agent path as ask_stream."""
    buf: list[str] = []
    async for evt in ask_stream(question, history, username, request):
        kind = evt.get("type")
        if kind == "text":
            buf.append(evt.get("content", ""))
        elif kind == "reset":
            buf.clear()
        elif kind == "error":
            raise ChatServiceError(evt.get("content", "Chat failed."))
    return "".join(buf).strip() or "I couldn't find an answer for that."
