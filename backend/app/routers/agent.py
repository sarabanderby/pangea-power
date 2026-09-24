"""Conversational agent proxy: forwards a grounded chat request to the CPU-served
LLM (OpenAI-compatible). The UI sends the current dashboard state as context so
answers are grounded in real values rather than guessed. Tool-calling is layered
on next; for now this is grounded chat only."""
from __future__ import annotations

import json

import httpx
from fastapi import APIRouter, HTTPException

from ..config import settings
from ..models import AgentChatRequest, AgentChatResponse

router = APIRouter(prefix="/api/agent", tags=["agent"])


def _build_messages(req: AgentChatRequest) -> list[dict]:
    messages = [{"role": "system", "content": settings.agent_system_prompt}]
    if req.ui_context:
        messages.append({
            "role": "system",
            "content": "Current dashboard state (JSON):\n"
            + json.dumps(req.ui_context, default=str),
        })
    for turn in (req.history or [])[-6:]:
        if turn.get("role") in ("user", "assistant") and turn.get("content"):
            messages.append({"role": turn["role"], "content": turn["content"]})
    messages.append({"role": "user", "content": req.message})
    return messages


@router.get("/health")
async def health() -> dict:
    """Report whether the LLM endpoint is reachable, for the UI status dot."""
    models_url = settings.agent_llm_url.replace("/chat/completions", "/models")
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(models_url)
            resp.raise_for_status()
    except httpx.HTTPError:
        return {"online": False, "model": settings.agent_llm_model}
    return {"online": True, "model": settings.agent_llm_model}


@router.post("/chat", response_model=AgentChatResponse)
async def chat(req: AgentChatRequest) -> AgentChatResponse:
    if not req.message.strip():
        raise HTTPException(status_code=400, detail="Empty message.")

    payload = {
        "model": settings.agent_llm_model,
        "messages": _build_messages(req),
        "max_tokens": settings.agent_max_tokens,
        "temperature": settings.agent_temperature,
        "frequency_penalty": settings.agent_frequency_penalty,
        "presence_penalty": settings.agent_presence_penalty,
        # vLLM-specific: penalises repeating prompt+output tokens.
        "repetition_penalty": settings.agent_repetition_penalty,
    }
    try:
        async with httpx.AsyncClient(timeout=settings.agent_http_timeout) as client:
            resp = await client.post(settings.agent_llm_url, json=payload)
            resp.raise_for_status()
            body = resp.json()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=503, detail="Assistant model unavailable.") from exc

    try:
        reply = (body["choices"][0]["message"]["content"] or "").strip()
    except (KeyError, IndexError, TypeError):
        reply = ""
    return AgentChatResponse(reply=reply, model=body.get("model"))
