"""
connectors/proxy/proxy.py — Chat Proxy
=======================================

A FastAPI service (port 8001) that bridges chat platforms to meMCP via
LLM tool-calling (Groq or Ollama, configured via config.tech.yaml).

Bot adapters (Telegram, Slack, Discord) are intentionally dumb — they just
POST { chat_id, message } here and forward the reply back to the user.
All state, auth, and AI orchestration live in this service.

Security
--------
  PROXY_SECRET (.env)  — shared secret between bot adapters and this proxy.
                         Every POST /chat must include header X-Proxy-Secret.
                         If unset: no auth check (development mode, logged as warning).

  Token encryption     — meMCP tokens are stored Fernet-encrypted in proxy.db
                         using a key derived from PROXY_SECRET.
                         If PROXY_SECRET is unset: stored plaintext (with warning).

Session state machine
---------------------
  NEEDS_TOKEN    → welcome message, ask for token → AWAITING_TOKEN
  AWAITING_TOKEN → treat next message as token → verify → ACTIVE or stay
  ACTIVE         → LLM ↔ meMCP tool calling loop

Special commands (any state): /disconnect, /status

Endpoints
---------
  POST /chat   { "chat_id": str, "message": str } → { "reply": str, "state": str }
  GET  /health                                     → { "status": str, ... }

Config (config.tech.yaml → chat: section)
------------------------------------------
  host:                 groq | ollama
  model:                model name
  ollama_url:           Ollama base URL    (default http://localhost:11434)
  db_path:              SQLite path        (default data/proxy.db)
  # meMCP URL is derived from server.port; override via MEMCP_URL env var
  rate_limit_per_minute: max msgs/chat_id  (default 20)
  max_history:          conversation turns (default 10)
  max_input_chars / max_output_chars: truncation limits

Secrets (.env)
--------------
  GROQ_API_KEY   required when host=groq
  PROXY_SECRET   shared secret for bot adapter → proxy auth + token encryption
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

import httpx
import yaml
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Header
from pydantic import BaseModel

import auth

# ── Bootstrap ────────────────────────────────────────────────────────────────

_ENV_FILE = Path(__file__).parent.parent / ".env"
load_dotenv(_ENV_FILE)

logger = logging.getLogger(__name__)


# ── Config loader ─────────────────────────────────────────────────────────────

def _load_tech_config() -> dict:
    """Load config.tech.yaml, searching common locations."""
    candidates = [
        os.getenv("CONFIG_PATH"),
        "/config/config.tech.yaml",
        str(Path(__file__).parent.parent.parent / "config.tech.yaml"),
    ]
    for path in candidates:
        if path and Path(path).exists():
            with open(path) as fh:
                return yaml.safe_load(fh) or {}
    logger.warning("config.tech.yaml not found — using defaults")
    return {}


_TECH_CFG = _load_tech_config()
_CFG      = _TECH_CFG.get("chat", {})

LLM_HOST    = _CFG.get("host", "groq")
LLM_MODEL   = _CFG.get("model", "llama-3.3-70b-versatile")
OLLAMA_URL  = _CFG.get("ollama_url", "http://localhost:11434").rstrip("/")
MAX_HISTORY = int(_CFG.get("max_history", 10))
MAX_IN      = int(_CFG.get("max_input_chars", 2000))
MAX_OUT     = int(_CFG.get("max_output_chars", 3000))
RATE_LIMIT  = int(_CFG.get("rate_limit_per_minute", 20))
MAX_ROUNDS  = 5

# MEMCP_URL: derived from server.port so there's no duplication in config.
# Override at runtime via MEMCP_URL env var for non-local deployments.
_server_port = _TECH_CFG.get("server", {}).get("port", 8000)
MEMCP_URL    = (os.getenv("MEMCP_URL") or f"http://localhost:{_server_port}").rstrip("/")
DB_PATH      = os.getenv("PROXY_DB_PATH") or _CFG.get("db_path", "data/proxy.db")
GROQ_KEY     = os.getenv("GROQ_API_KEY", "")
PROXY_SECRET = os.getenv("PROXY_SECRET", "")

SYSTEM_PROMPT = (
    "You are a conversational assistant for a personal profile server. "
    "Answer questions about the person's background, experience, projects, and skills. "
    "You have tools available to query the profile database — use them when you need data. "
    "Format replies naturally for chat. Keep responses concise (2-4 sentences unless "
    "more detail is requested). If a tool call returns an error about access tier, "
    "explain that the user would need a higher-tier token for that feature."
)


# ── Rate limiter (in-memory sliding window per chat_id) ──────────────────────

_rate_windows: dict[str, list[float]] = defaultdict(list)


def _is_rate_limited(chat_id: str) -> bool:
    """
    Return True if *chat_id* has exceeded RATE_LIMIT messages in the last 60 s.
    Removes expired timestamps as a side effect.
    """
    now = time.monotonic()
    window = _rate_windows[chat_id]
    window[:] = [t for t in window if now - t < 60.0]
    if len(window) >= RATE_LIMIT:
        return True
    window.append(now)
    return False


# ── LLM client ───────────────────────────────────────────────────────────────

class _LLMClient:
    """Thin abstraction over Groq and Ollama (OpenAI-compatible) backends."""

    def __init__(self) -> None:
        self.host = LLM_HOST
        self.model = LLM_MODEL
        self._groq = None

    def _get_groq(self):
        if self._groq is None:
            if not GROQ_KEY:
                raise RuntimeError(
                    "GROQ_API_KEY is not set. "
                    "Add it to connectors/.env or set it as an environment variable."
                )
            import groq as groq_sdk
            self._groq = groq_sdk.Groq(api_key=GROQ_KEY)
        return self._groq

    def complete(self, messages: list[dict], tools: Optional[list[dict]] = None) -> dict:
        if self.host == "groq":
            return self._complete_groq(messages, tools)
        return self._complete_ollama(messages, tools)

    def _complete_groq(self, messages, tools):
        groq = self._get_groq()
        kwargs: dict = dict(model=self.model, messages=messages, temperature=0.7, max_tokens=1000)
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        resp = groq.chat.completions.create(**kwargs)
        choice = resp.choices[0]
        return {
            "content": choice.message.content or "",
            "tool_calls": [
                {"id": tc.id, "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                for tc in (choice.message.tool_calls or [])
            ],
        }

    def _complete_ollama(self, messages, tools):
        payload: dict = {"model": self.model, "messages": messages, "stream": False, "options": {"temperature": 0.7}}
        if tools:
            payload["tools"] = tools
        resp = httpx.post(f"{OLLAMA_URL}/v1/chat/completions", json=payload, timeout=60)
        resp.raise_for_status()
        choice = resp.json()["choices"][0]["message"]
        raw_calls = choice.get("tool_calls") or []
        return {
            "content": choice.get("content") or "",
            "tool_calls": [
                {
                    "id": tc.get("id", f"call_{i}"),
                    "function": {
                        "name": tc["function"]["name"],
                        "arguments": tc["function"]["arguments"]
                        if isinstance(tc["function"]["arguments"], str)
                        else json.dumps(tc["function"]["arguments"]),
                    },
                }
                for i, tc in enumerate(raw_calls)
            ],
        }


_llm = _LLMClient()


# ── Tool manifest ─────────────────────────────────────────────────────────────

_groq_tools: list[dict] = []


async def _fetch_tool_manifest() -> None:
    global _groq_tools
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{MEMCP_URL}/mcp/tools")
            resp.raise_for_status()
            tools = resp.json()["data"]["tools"]
            _groq_tools = [
                {"type": "function", "function": {"name": t["name"], "description": t["description"], "parameters": t["inputSchema"]}}
                for t in tools
            ]
            logger.info("Loaded %d tools from meMCP (backend: %s/%s)", len(_groq_tools), LLM_HOST, LLM_MODEL)
    except Exception as exc:
        logger.warning("Could not fetch meMCP tool manifest: %s", exc)
        _groq_tools = []


# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(level=logging.INFO)
    if not PROXY_SECRET:
        logger.warning("PROXY_SECRET is not set — proxy accepts requests from any caller (development mode)")
    auth.init_db(DB_PATH, secret=PROXY_SECRET)
    await _fetch_tool_manifest()
    yield


app = FastAPI(title="meMCP Chat Proxy", version="1.0.0", lifespan=lifespan)


# ── Auth dependency ────────────────────────────────────────────────────────────

def _verify_proxy_secret(x_proxy_secret: str = Header("")) -> None:
    """FastAPI dependency: reject requests with wrong proxy secret."""
    if PROXY_SECRET and x_proxy_secret != PROXY_SECRET:
        raise HTTPException(401, "Invalid or missing X-Proxy-Secret header")


# ── meMCP helpers ─────────────────────────────────────────────────────────────

def _truncate(text: str, max_chars: int) -> str:
    return text if len(text) <= max_chars else text[:max_chars - 1] + "…"


async def _verify_token(token: str) -> dict | None:
    """
    Verify a token via /token/info and check it's a 'chat'-tier token.

    Returns the token info dict on success, None on failure.
    """
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.get(
                f"{MEMCP_URL}/token/info",
                headers={"Authorization": f"Bearer {token}"},
            )
            if resp.status_code != 200:
                return None
            data = resp.json().get("data", {})
            tier = data.get("tier", "")
            # Accept both 'chat' (correct) and 'mcp' (backward compat during transition)
            if tier not in ("chat", "mcp"):
                return None
            return data
    except Exception:
        return None


async def _derive_token(chat_token: str, chat_id: str) -> str | None:
    """
    Call the meMCP internal endpoint to create a short-lived derived token
    for MCP API calls. Returns the raw derived token or None on failure.
    """
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.post(
                f"{MEMCP_URL}/internal/tokens/derive",
                headers={
                    "X-Proxy-Secret": PROXY_SECRET,
                    "Content-Type": "application/json",
                },
                json={
                    "parent_token": chat_token,
                    "scope": "mcp_read",
                    "ttl_minutes": 60,
                    "chat_id": chat_id,
                },
            )
            if resp.status_code != 200:
                logger.warning("Failed to derive token: %s %s", resp.status_code, resp.text[:200])
                return None
            return resp.json().get("derived_token")
    except Exception as exc:
        logger.warning("Derive token error: %s", exc)
        return None


async def _revoke_derived_token(derived_token: str) -> None:
    """Revoke a derived token when disconnecting."""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            await client.post(
                f"{MEMCP_URL}/internal/tokens/revoke",
                headers={
                    "X-Proxy-Secret": PROXY_SECRET,
                    "Content-Type": "application/json",
                },
                json={"derived_token": derived_token},
            )
    except Exception as exc:
        logger.debug("Revoke derived token error (non-critical): %s", exc)


async def _call_tool(token: str, tool_name: str, arguments: dict) -> dict:
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{MEMCP_URL}/mcp/tools/call",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json={"tool": tool_name, "arguments": arguments},
            )
            if resp.status_code == 403:
                return {"error": "access_denied", "message": "This feature requires a higher-tier token."}
            if resp.status_code != 200:
                return {"error": f"http_{resp.status_code}", "message": resp.text[:200]}
            return {"result": resp.json()}
    except Exception as exc:
        return {"error": "network_error", "message": str(exc)}


# ── LLM orchestration loop ────────────────────────────────────────────────────

async def _llm_loop(token: str, history: list[dict], user_message: str) -> tuple[str, list[dict]]:
    trimmed_input = _truncate(user_message, MAX_IN)
    history = history[-(MAX_HISTORY * 2):]
    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + history + [{"role": "user", "content": trimmed_input}]
    new_history = history + [{"role": "user", "content": trimmed_input}]

    for _ in range(MAX_ROUNDS):
        choice = _llm.complete(messages, tools=_groq_tools or None)

        if not choice["tool_calls"]:
            reply = _truncate(choice["content"], MAX_OUT)
            new_history.append({"role": "assistant", "content": reply})
            return reply, new_history

        assistant_msg: dict = {
            "role": "assistant",
            "content": choice["content"],
            "tool_calls": [{"id": tc["id"], "type": "function", "function": tc["function"]} for tc in choice["tool_calls"]],
        }
        messages.append(assistant_msg)

        for tc in choice["tool_calls"]:
            try:
                args = json.loads(tc["function"]["arguments"])
            except json.JSONDecodeError:
                args = {}
            tool_result = await _call_tool(token, tc["function"]["name"], args)
            messages.append({"role": "tool", "tool_call_id": tc["id"], "content": json.dumps(tool_result, ensure_ascii=False)})

    choice = _llm.complete(messages + [{"role": "user", "content": "Please summarise what you found."}])
    reply = _truncate(choice["content"] or "I found some data — please ask me to summarise.", MAX_OUT)
    new_history.append({"role": "assistant", "content": reply})
    return reply, new_history


# ── Session state machine ─────────────────────────────────────────────────────

WELCOME    = "Hi! I'm a chat interface to a personal profile server.\nPaste your meMCP access token to get started."
TOKEN_OK   = "Connected! Try: \"what are your top skills?\" or \"tell me about yourself.\""
TOKEN_BAD  = "That token didn't work. Please try again, or ask the profile owner for a valid token."
DISCONNECT = "Disconnected. Paste your meMCP access token to reconnect."


async def _handle_message(chat_id: str, message: str) -> tuple[str, str]:
    msg = message.strip()
    session = auth.get_session(chat_id)

    if msg == "/disconnect":
        # Revoke derived token before clearing session
        if session and session.get("token"):
            await _revoke_derived_token(session["token"])
        auth.clear_session(chat_id)
        return DISCONNECT, "needs_token"

    if msg == "/status":
        state = session["state"] if session else "needs_token"
        token_info = "set" if (session and session.get("token")) else "not set"
        return f"State: {state} | Token: {token_info} | Backend: {LLM_HOST}/{LLM_MODEL}", state

    if session is None or session["state"] == "needs_token":
        auth.upsert_session(chat_id, state="awaiting_token")
        return WELCOME, "awaiting_token"

    if session["state"] == "awaiting_token":
        token_data = await _verify_token(msg)
        if token_data:
            # Token verified — now derive a scoped MCP token for API calls
            derived = await _derive_token(msg, chat_id)
            if derived:
                # Store the derived token (not the original chat token)
                auth.upsert_session(chat_id, token=derived, state="active", history=[])
                return TOKEN_OK, "active"
            else:
                # Derivation failed — fall back to using the chat token directly
                logger.warning("Token derivation failed for %s — using chat token directly", chat_id)
                auth.upsert_session(chat_id, token=msg, state="active", history=[])
                return TOKEN_OK, "active"
        return TOKEN_BAD, "awaiting_token"

    try:
        reply, updated_history = await _llm_loop(session["token"], session["history"], msg)
    except Exception as exc:
        logger.error("LLM loop error for %s: %s", chat_id, exc)
        return "Sorry, something went wrong with the AI backend. Please try again.", "active"

    auth.upsert_session(chat_id, history=updated_history)
    return reply, "active"


# ── Endpoints ─────────────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    chat_id: str
    message: str


class ChatResponse(BaseModel):
    reply: str
    state: str


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest, x_proxy_secret: str = Header("")):
    _verify_proxy_secret(x_proxy_secret)

    if not req.chat_id:
        raise HTTPException(400, "chat_id is required")
    if not req.message:
        raise HTTPException(400, "message is required")

    if _is_rate_limited(req.chat_id):
        raise HTTPException(429, f"Rate limit exceeded: max {RATE_LIMIT} messages per minute per user")

    reply, state = await _handle_message(req.chat_id, req.message)
    return ChatResponse(reply=reply, state=state)


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "backend": f"{LLM_HOST}/{LLM_MODEL}",
        "tools_loaded": len(_groq_tools),
        "memcp_url": MEMCP_URL,
        "rate_limit_per_minute": RATE_LIMIT,
        "auth": "enabled" if PROXY_SECRET else "disabled (no PROXY_SECRET set)",
    }
