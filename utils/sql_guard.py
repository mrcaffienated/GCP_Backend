"""SQL guardrails for the chatbot.

Only plain read-only SELECT statements may reach the database. Everything else
is rejected BEFORE execution:

  1. Comments are stripped, string literals blanked, whitespace normalised.
  2. The statement must START with SELECT (WITH/CTE is also allowed, but is
     still subject to the keyword scan below).
  3. Exactly ONE statement — no ";" chaining.
  4. Every word is scanned against a forbidden-keyword list (DELETE, INSERT,
     UPDATE, DROP, ...). If any forbidden keyword appears anywhere, reject.

This is defence layer 1. Layer 2 is the repo executing inside a READ ONLY
transaction, so even a query that slipped past the scan physically cannot
write — Postgres itself rejects it.
"""
import re

# Any of these words appearing ANYWHERE in the query → rejected.
FORBIDDEN_KEYWORDS = frozenset({
    "insert", "update", "delete", "drop", "alter", "truncate", "create",
    "grant", "revoke", "copy", "vacuum", "reindex", "cluster", "comment",
    "merge", "call", "do", "execute", "prepare", "deallocate", "listen",
    "notify", "load", "lock", "refresh", "reset", "discard", "checkpoint",
    "begin", "commit", "rollback", "savepoint", "release", "abort", "start",
    "set", "security", "owner", "pg_sleep", "pg_terminate_backend",
    "pg_cancel_backend", "pg_read_file", "pg_write_file", "lo_import",
    "lo_export", "dblink", "import",
})

_COMMENT_LINE = re.compile(r"--[^\n]*")
_COMMENT_BLOCK = re.compile(r"/\*.*?\*/", re.DOTALL)
_STRING_LITERAL = re.compile(r"'(?:[^']|'')*'")          # 'text' with '' escapes
_DOLLAR_QUOTED = re.compile(r"\$[a-zA-Z0-9_]*\$.*?\$[a-zA-Z0-9_]*\$", re.DOTALL)
_WORD = re.compile(r"[a-zA-Z_][a-zA-Z0-9_]*")


class SQLGuardError(Exception):
    """Raised when a query fails the guardrails. Message is safe to show."""


def _normalize(sql: str) -> str:
    """Strip comments and blank out string literals so the keyword scan can't
    be fooled by (or false-positive on) text inside quotes/comments."""
    sql = _COMMENT_BLOCK.sub(" ", sql)
    sql = _COMMENT_LINE.sub(" ", sql)
    sql = _DOLLAR_QUOTED.sub("''", sql)
    sql = _STRING_LITERAL.sub("''", sql)
    return re.sub(r"\s+", " ", sql).strip()


def validate_select_only(sql: str) -> str:
    """Validate a query against the guardrails.

    Returns the original (trimmed) SQL if safe.
    Raises SQLGuardError with a clear reason otherwise.
    """
    if not sql or not sql.strip():
        raise SQLGuardError("Empty query.")

    original = sql.strip().rstrip(";").strip()
    cleaned = _normalize(original)
    if not cleaned:
        raise SQLGuardError("Query contains no executable SQL.")

    # Rule: single statement only — no chaining a second statement after ";".
    if ";" in cleaned:
        raise SQLGuardError("Multiple SQL statements are not allowed.")

    lowered = cleaned.lower()

    # Rule: must start with SELECT (or WITH for CTEs — still keyword-scanned).
    first_word = lowered.split(" ", 1)[0]
    if first_word not in ("select", "with"):
        raise SQLGuardError(
            f"Only SELECT queries are allowed — this query starts with '{first_word.upper()}'."
        )

    # Rule: forbidden keyword scan over every word in the query.
    for word in _WORD.findall(lowered):
        if word in FORBIDDEN_KEYWORDS:
            raise SQLGuardError(
                f"Blocked: query contains the forbidden keyword '{word.upper()}'. "
                "Only read-only SELECT queries are allowed."
            )

    return original
