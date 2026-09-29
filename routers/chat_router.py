"""Chatbot endpoints.

POST /api/chat/       — non-streaming (backward compat)
POST /api/chat/stream — streaming via Server-Sent Events

Layering: router (here) → services/chat_service → repo/chat_repo → utils/sql_guard.
Requires login (same JWT as the rest of the app).
"""
import json
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from utils.jwt_utils import get_current_user
from services import chat_service

router = APIRouter()


class ChatTurn(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    history: Optional[List[ChatTurn]] = None


class ChatResponse(BaseModel):
    answer: str


# ── Non-streaming endpoint (backward compat) ──────────────────────────────────

@router.post("/", response_model=ChatResponse)
async def chat(
    body: ChatRequest,
    request: Request,
    user: dict = Depends(get_current_user),
):
    try:
        answer = await chat_service.ask(
            question=body.message.strip(),
            history=[t.model_dump() for t in (body.history or [])],
            username=user.get("sub", "?"),
            request=request,
        )
        return ChatResponse(answer=answer)
    except chat_service.ChatNotConfiguredError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except chat_service.ChatServiceError as e:
        raise HTTPException(status_code=502, detail=str(e))


# ── Streaming endpoint (SSE) ─────────────────────────────────────────────────

@router.post("/stream")
async def chat_stream(
    body: ChatRequest,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """Stream chat responses as Server-Sent Events.

    Each event is a JSON object with a "type" field:
      {"type": "thinking", "content": "Looking up data…"}
      {"type": "text", "content": "chunk of answer"}
      {"type": "done"}
      {"type": "error", "content": "error message"}
    """
    username = user.get("sub", "?")

    async def event_generator():
        try:
            async for event in chat_service.ask_stream(
                question=body.message.strip(),
                history=[t.model_dump() for t in (body.history or [])],
                username=username,
                request=request,
            ):
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except chat_service.ChatNotConfiguredError as e:
            yield f"data: {json.dumps({'type': 'error', 'content': str(e)})}\n\n"
            yield f"data: {json.dumps({'type': 'done'})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'content': 'Chat service error.'})}\n\n"
            yield f"data: {json.dumps({'type': 'done'})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # Nginx: don't buffer the stream.
            "X-Accel-Buffering": "no",
            # CRITICAL for streaming: GZipMiddleware buffers a compressed stream
            # inside its zlib window and only flushes on close, which would
            # deliver every SSE event in one burst at the end (no live effect).
            # Declaring an encoding here makes GZipResponder pass each chunk
            # through untouched, so tokens reach the browser the instant they're
            # produced.
            "Content-Encoding": "identity",
        },
    )
