"""Conversational agent proxy: forwards a grounded chat request (dashboard state
as context) to the OpenAI-compatible LLM. Blocking /chat and SSE /chat/stream."""
from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from .. import laya
from ..config import settings
from ..models import AgentChatRequest, AgentChatResponse
from . import operators

router = APIRouter(prefix="/api/agent", tags=["agent"])


def _build_messages(req: AgentChatRequest, context: dict | None) -> list[dict]:
    messages = [{"role": "system", "content": settings.agent_system_prompt}]
    if context:
        messages.append({
            "role": "system",
            "content": "Current dashboard state (JSON):\n"
            + json.dumps(context, default=str),
        })
    for turn in (req.history or [])[-6:]:
        if turn.get("role") in ("user", "assistant") and turn.get("content"):
            messages.append({"role": turn["role"], "content": turn["content"]})
    messages.append({"role": "user", "content": req.message})
    return messages


def _route_state(req: AgentChatRequest) -> str:
    prev_user = ""
    for turn in reversed(req.history or []):
        if turn.get("role") == "user" and turn.get("content"):
            prev_user = turn["content"]
            break
    return f"{prev_user}\n{req.message}".strip() if prev_user else req.message


async def _resolve_context(req: AgentChatRequest) -> tuple[dict | None, dict | None]:
    context = req.ui_context
    route_info = None
    if req.ui_context:
        route_info = await laya.route(_route_state(req), req.ui_context)
        if (route_info and route_info["tool"] and route_info["tool"] != "none"
                and route_info["tool_confidence"] >= settings.laya_confidence_floor):
            if route_info["tool"] == "operators":
                ops = await operators.available_operators()
                context = {
                    "at": req.ui_context.get("at"),
                    "operators": [o.model_dump() for o in ops],
                }
            else:
                context = laya.select_context(
                    req.ui_context, route_info["tool"], route_info["site"])
    return context, route_info


def _build_payload(req: AgentChatRequest, context: dict | None, stream: bool) -> dict:
    return {
        "model": settings.agent_llm_model,
        "messages": _build_messages(req, context),
        "max_tokens": settings.agent_max_tokens,
        "temperature": settings.agent_temperature,
        "frequency_penalty": settings.agent_frequency_penalty,
        "presence_penalty": settings.agent_presence_penalty,
        "repetition_penalty": settings.agent_repetition_penalty,
        "stream": stream,
    }


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

    context, route_info = await _resolve_context(req)
    payload = _build_payload(req, context, stream=False)
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
    return AgentChatResponse(reply=reply, model=body.get("model"), route=route_info)


def _sse(obj: dict) -> str:
    return f"data: {json.dumps(obj)}\n\n"


async def _stream_reply(payload: dict, route_info: dict | None) -> AsyncIterator[str]:
    if route_info:
        yield _sse({"route": route_info})
    try:
        async with httpx.AsyncClient(timeout=settings.agent_http_timeout) as client:
            async with client.stream("POST", settings.agent_llm_url, json=payload) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        delta = json.loads(data)["choices"][0]["delta"].get("content")
                    except (KeyError, IndexError, ValueError, TypeError):
                        continue
                    if delta:
                        yield _sse({"delta": delta})
    except httpx.HTTPError:
        yield _sse({"error": "Assistant model unavailable."})
    yield _sse({"done": True})


@router.post("/chat/stream")
async def chat_stream(req: AgentChatRequest) -> StreamingResponse:
    if not req.message.strip():
        raise HTTPException(status_code=400, detail="Empty message.")

    context, route_info = await _resolve_context(req)
    payload = _build_payload(req, context, stream=True)
    return StreamingResponse(
        _stream_reply(payload, route_info),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
