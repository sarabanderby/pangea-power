"""Conversational agent proxy: forwards a grounded chat request (dashboard state
as context) to the OpenAI-compatible LLM. Blocking /chat and SSE /chat/stream."""
from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import AsyncIterator
from functools import lru_cache
from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from .. import db, laya, tracing
from ..config import settings
from ..models import AgentChatRequest, AgentChatResponse
from . import operators

router = APIRouter(prefix="/api/agent", tags=["agent"])

logger = logging.getLogger("pangea.agent")

_site_reference: list[dict] | None = None


async def _load_site_reference() -> list[dict]:
    global _site_reference
    if _site_reference is None:
        rows = await db.fetch(
            "SELECT site_name, site_code, energy_type::text AS energy_type, "
            "       country, nearest_city, latitude, longitude "
            "FROM sites WHERE is_active ORDER BY site_name"
        )
        _site_reference = [dict(r) for r in rows]
    return _site_reference


def _build_messages(req: AgentChatRequest, context: dict | None,
                    site_ref: list[dict] | None,
                    directive: str | None = None) -> list[dict]:
    messages = [{"role": "system", "content": settings.agent_system_prompt}]
    # A directive (e.g. the alerts tool) supersedes the generic state dump and
    # is self-contained, so we drop the site reference, the JSON context and the
    # history replay to stop the model answering from anything but the directive.
    if directive:
        messages.append({"role": "system", "content": directive})
    else:
        if site_ref:
            places = [{k: s.get(k) for k in
                       ("site_name", "site_code", "country", "nearest_city")}
                      for s in site_ref]
            messages.append({
                "role": "system",
                "content": "Authoritative site locations (use these for any country "
                "or city question; do not guess):\n" + json.dumps(places, default=str),
            })
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


_SEVERITIES = ("emergency", "critical", "warning", "info")
_WO_PRIORITIES = ("critical", "high", "medium", "low")


def _list_directive(noun: str, scope: str, items: list) -> str:
    """Deterministic list tool: hand the model the exact, pre-filtered list so
    enumeration and filtering are done in code, not left to the model."""
    if not items:
        return (f"The user asked to list {scope}{noun}. There are none matching right "
                "now. Tell the user there are none; do not invent any.")
    return (
        f"The user asked to list {scope}{noun}. Below is the COMPLETE, already-filtered "
        "list to report. Report every one of these and nothing else — do not add, omit, "
        "merge, or invent entries. Present them as a short list.\n"
        + json.dumps(items, default=str)
    )


def _alerts_directive(req: AgentChatRequest) -> str:
    alerts = req.ui_context.get("active_alerts") or []
    wanted = {s for s in _SEVERITIES if s in req.message.lower()}
    if wanted:
        alerts = [a for a in alerts if (a.get("severity") or "").lower() in wanted]
    scope = "/".join(sorted(wanted)) + " " if wanted else ""
    logger.info("alerts tool | wanted=%s -> %d alert(s)", wanted or "all", len(alerts))
    return _list_directive("alerts", scope, alerts)


def _maintenance_directive(req: AgentChatRequest) -> str:
    tasks = req.ui_context.get("maintenance_tasks") or []
    wanted = {p for p in _WO_PRIORITIES if p in req.message.lower()}
    if wanted:
        tasks = [t for t in tasks if (t.get("priority") or "").lower() in wanted]
    scope = "/".join(sorted(wanted)) + " priority " if wanted else ""
    logger.info("maintenance tool | wanted=%s -> %d task(s)", wanted or "all", len(tasks))
    return _list_directive("maintenance tasks", scope, tasks)


_FREE_RE = re.compile(
    r"\b(free|available|spare|idle|unassigned|not busy|who can|anyone|"
    r"somebody|someone)\b", re.I)
_SHIFT_TERMS = {"night": "night", "nights": "night", "on call": "on_call",
                "on-call": "on_call", "day shift": "day", "days": "day"}
_CERT_TERMS = {"rope access": "rope_access", "offshore": "offshore_safety",
               "high voltage": "high_voltage", "high-voltage": "high_voltage",
               "electrical": "electrical", "mechanical": "mechanical",
               "hydraulic": "hydraulic", "blade": "blade_repair"}
_OPERATOR_BRIEF = ("name", "skill_level", "base_location", "today_shift",
                   "open_assignments", "current_task")
_OPERATOR_FULL = _OPERATOR_BRIEF + (
    "employee_code", "certifications", "specializations", "offshore_certified",
    "weekly_schedule", "exception_dates", "upcoming_assignments")


def _named_operators(message: str, rows: list[dict]) -> list[dict]:
    low = message.lower()
    hits = []
    for r in rows:
        name = (r.get("name") or "").lower()
        surname = name.rsplit(" ", 1)[-1] if " " in name else name
        if name and (name in low or (len(surname) > 3 and surname in low)):
            hits.append(r)
    return hits


def _operators_directive(req: AgentChatRequest, ops: list) -> str:
    rows = [o.model_dump() for o in ops]
    low = req.message.lower()
    scope = ""

    named = _named_operators(req.message, rows)
    if named:
        slim = [{k: r.get(k) for k in _OPERATOR_FULL} for r in named]
        who = ", ".join(r["name"] for r in named)
        logger.info("operators tool | named=%s", who)
        return (
            f"The user asked about {who}. Below is their complete record. Answer "
            "ONLY about them, in at most two sentences, using these fields: "
            "today_shift is the shift they are working today ('off' means not "
            "working), open_assignments is how many jobs they already hold, and "
            "current_task is what they are doing right now. Do not list other "
            "operators and do not invent anyone.\n"
            + json.dumps(slim, default=str)
        )

    shift = next((v for k, v in _SHIFT_TERMS.items() if k in low), None)
    if shift:
        rows = [r for r in rows if (r.get("today_shift") or "").lower() == shift]
        scope = f"{shift.replace('_', '-')} shift "
    elif _FREE_RE.search(low):
        rows = [r for r in rows if (r.get("today_shift") or "").lower() != "off"]
        rows.sort(key=lambda r: (r.get("open_assignments") or 0, r["name"]))
        scope = "on-shift "

    cert = next((v for k, v in _CERT_TERMS.items() if k in low), None)
    if cert:
        rows = [r for r in rows if cert in (r.get("certifications") or [])]
        scope += f"{cert.replace('_', ' ')}-certified "

    fields = _OPERATOR_FULL if len(rows) <= 3 else _OPERATOR_BRIEF
    slim = [{k: r.get(k) for k in fields} for r in rows]
    logger.info("operators tool | scope=%s -> %d operator(s)", scope or "all", len(slim))
    return _list_directive("operators/technicians", scope, slim)


_WEATHER_RE = re.compile(
    r"\b(?:weather|temperature|rain|rains|raining|rainy|snow|snowing|snowy|"
    r"humidity|humid|sunny|cloudy|overcast|fog|foggy|thunderstorm|degrees|"
    r"celsius|fahrenheit)\b|\bhow (?:hot|cold|warm) ",
    re.IGNORECASE,
)

_PLACE_RE = re.compile(
    r"\b(?:in|at|for|near|around)\s+"
    r"(?P<place>[A-Za-zÀ-ÿ0-9'’.\-]+(?:[ \-][A-Za-zÀ-ÿ0-9'’.\-]+){0,3})",
    re.IGNORECASE,
)
_PLACE_STOP = re.compile(
    r"\s+(?:right now|now|today|tonight|tomorrow|currently|please|"
    r"at the moment|this (?:morning|afternoon|evening))\b.*$",
    re.IGNORECASE,
)

_WMO = {
    0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast",
    45: "fog", 48: "freezing fog", 51: "light drizzle", 53: "drizzle",
    55: "heavy drizzle", 56: "freezing drizzle", 57: "heavy freezing drizzle",
    61: "light rain", 63: "rain", 65: "heavy rain", 66: "freezing rain",
    67: "heavy freezing rain", 71: "light snow", 73: "snow", 75: "heavy snow",
    77: "snow grains", 80: "light rain showers", 81: "rain showers",
    82: "violent rain showers", 85: "snow showers", 86: "heavy snow showers",
    95: "thunderstorm", 96: "thunderstorm with hail", 99: "thunderstorm with heavy hail",
}

_weather_cache: dict[str, tuple[float, dict]] = {}


def _wants_weather(message: str) -> bool:
    return bool(_WEATHER_RE.search(message or ""))


def _extract_place(message: str) -> str | None:
    m = _PLACE_RE.search(message or "")
    if not m:
        return None
    return _PLACE_STOP.sub("", m.group("place")).strip(" ?.,!'\"") or None


def _match_site(place: str, sites: list[dict]) -> dict | None:
    low = place.lower()
    for s in sites:
        for field in ("site_name", "site_code", "nearest_city"):
            if (s.get(field) or "").lower() == low:
                return s
    for s in sites:
        name = (s.get("site_name") or "").lower()
        if name and (low in name or name in low):
            return s
    return None


async def _weather_lookup(place: str, sites: list[dict]) -> dict | None:
    key = place.strip().lower()
    hit = _weather_cache.get(key)
    if hit and time.monotonic() - hit[0] < settings.weather_cache_ttl_seconds:
        return hit[1]

    site = _match_site(place, sites)
    try:
        async with httpx.AsyncClient(timeout=settings.weather_http_timeout) as client:
            if site and site.get("latitude") is not None:
                lat, lon = float(site["latitude"]), float(site["longitude"])
                label = " · ".join(str(p) for p in (site.get("site_name"),
                                   site.get("nearest_city"), site.get("country")) if p)
            else:
                resp = await client.get(settings.open_meteo_geocoding_url, params={
                    "name": place, "count": 1, "language": "en", "format": "json"})
                resp.raise_for_status()
                results = (resp.json() or {}).get("results") or []
                if not results:
                    logger.info("weather tool | place=%r -> not found", place)
                    return None
                top = results[0]
                lat, lon = top.get("latitude"), top.get("longitude")
                label = ", ".join(str(p) for p in (top.get("name"),
                                  top.get("admin1"), top.get("country")) if p)

            resp = await client.get(settings.open_meteo_url, params={
                "latitude": lat, "longitude": lon,
                "current": ("temperature_2m,apparent_temperature,relative_humidity_2m,"
                            "precipitation,weather_code,wind_speed_10m,"
                            "wind_gusts_10m,cloud_cover"),
                "wind_speed_unit": "ms", "timezone": "auto"})
            resp.raise_for_status()
            cur = (resp.json() or {}).get("current") or {}
    except (httpx.HTTPError, TypeError, ValueError) as exc:
        logger.warning("weather tool | place=%r failed: %s", place, exc)
        return None

    code = cur.get("weather_code")
    out = {
        "place": label,
        "local_time": cur.get("time"),
        "conditions": _WMO.get(code) if code is not None else None,
        "temperature_c": cur.get("temperature_2m"),
        "feels_like_c": cur.get("apparent_temperature"),
        "humidity_pct": cur.get("relative_humidity_2m"),
        "precipitation_mm": cur.get("precipitation"),
        "cloud_cover_pct": cur.get("cloud_cover"),
        "wind_ms": cur.get("wind_speed_10m"),
        "gust_ms": cur.get("wind_gusts_10m"),
        "source": "Open-Meteo",
    }
    _weather_cache[key] = (time.monotonic(), out)
    logger.info("weather tool | place=%r -> %s, %s°C, %s",
                place, out["place"], out["temperature_c"], out["conditions"])
    return out


def _weather_directive(place: str | None, data: dict | None) -> str:
    if not data:
        where = f" for {place}" if place else ""
        return (
            f"The user asked about the weather{where}. The weather service could not "
            "resolve that location. Say you could not find that place and ask which "
            "city or site they mean. Do NOT state any temperature or conditions, and "
            "do NOT say you lack real-time weather access."
        )
    return (
        "The user asked about the weather. You DO have live weather access; the "
        "current observation is below. Report it in one or two short sentences using "
        "only these values and never say you lack real-time data.\n"
        + json.dumps(data, default=str)
    )


_SOUL_FALLBACK = (
    "I am Pangea, the operations assistant for the Pangea Energy and Power "
    "fleet. I can read live site data, alerts, work orders and the operator "
    "roster, but I cannot act on the fleet myself."
)


@lru_cache(maxsize=1)
def _soul() -> str:
    try:
        text = Path(settings.soul_path).read_text(encoding="utf-8").strip()
    except OSError as exc:
        logger.warning("soul.md unreadable at %s (%s); using fallback",
                       settings.soul_path, exc)
        return _SOUL_FALLBACK
    return text or _SOUL_FALLBACK


def _identity_directive(req: AgentChatRequest) -> str:
    return (
        "The user asked about you. The document below is who you are; treat it "
        "as true about yourself and speak in the first person. Answer their "
        "question from it in two or three sentences, using whichever part of "
        "it their question is about. Do not repeat the opening paragraph when "
        "they asked about something else, do not offer further help, do not "
        "quote the headings, do not list the sites unless they asked which "
        "sites you cover, and do not mention this document.\n\n"
        + _soul()
    )


def _site_facts(snap: dict) -> list[str]:
    """Deterministic site summary: work the snapshot into finished sentences here
    so the model only has to join them up, never read the nested JSON."""
    name = snap.get("name") or "The site"
    out, cap = snap.get("live_output_mw"), snap.get("rated_capacity_mw")
    facts = []

    if out is not None and cap:
        facts.append(f"{name} is generating {out} MW of its {cap} MW capacity "
                     f"({round(out / cap * 100)}% of rated).")
    elif out is not None:
        facts.append(f"{name} is generating {out} MW.")

    t = snap.get("turbines") or {}
    if t.get("total"):
        facts.append(f"{t.get('operational') or 0} of {t['total']} turbines are "
                     f"running, {t.get('maintenance') or 0} in maintenance and "
                     f"{t.get('offline') or 0} offline.")

    if snap.get("shutdown"):
        facts.append(f"The site is shut down for high wind at "
                     f"{snap.get('wind_ms')} m/s against a "
                     f"{snap.get('cutout_ms')} m/s cut-out.")
    elif snap.get("wind_ms") is not None:
        facts.append(f"Wind is {snap['wind_ms']} m/s, gusting "
                     f"{snap.get('gust_ms')} m/s.")

    if snap.get("cloud_cover_pct") is not None:
        facts.append(f"Cloud cover is {snap['cloud_cover_pct']}%.")

    alerts = snap.get("open_alerts") or []
    if alerts:
        facts.append("Open alerts: " + "; ".join(
            f"{a.get('title')} ({a.get('severity')}"
            + (f" on {a['turbine']}" if a.get("turbine") else "") + ")"
            for a in alerts) + ".")
    else:
        facts.append("There are no open alerts.")

    work = snap.get("scheduled_work") or []
    if work:
        facts.append("Scheduled work: " + "; ".join(
            f"{w.get('title')} ({w.get('priority')} priority, {w.get('status')}, "
            + (w["assigned_operative"] if w.get("assigned_operative") else "unassigned")
            + ")" for w in work) + ".")
    else:
        facts.append("No maintenance is scheduled.")

    return facts


def _site_summary_directive(snap: dict) -> str:
    facts = _site_facts(snap)
    logger.info("site summary | site=%s -> %d fact(s)", snap.get("code"), len(facts))
    return (
        f"The user asked about {snap.get('name')}. Below are the facts for that "
        "site, already worked out for you. Report every one of them as a short "
        "paragraph in your own words, keeping the numbers exactly as written. Do "
        "not add anything, do not leave anything out, and do not mention any "
        "other site.\n"
        + "\n".join("- " + f for f in facts)
    )


async def _weather_route(req: AgentChatRequest, matched_by: str) -> str:
    with tracing.span("weather", tracing.TOOL) as trace_span:
        place = _extract_place(req.message)
        if not place and req.ui_context:
            open_site = req.ui_context.get("open_site") or {}
            place = open_site.get("city") or open_site.get("name")
        trace_span.set_inputs({"place": place, "matched_by": matched_by})
        data = await _weather_lookup(
            place, await _load_site_reference()) if place else None
        trace_span.set_outputs({"resolved": data is not None, "observation": data})
    return _weather_directive(place, data)


async def _resolve_context(
    req: AgentChatRequest,
) -> tuple[dict | None, dict | None, str | None]:
    context = req.ui_context
    route_info = None
    directive = None

    if _wants_weather(req.message):
        route_info = {"tool": "weather", "tool_confidence": 1.0, "site": None}
        tracing.tag(tool="weather", routed_by="regex")
        return context, route_info, await _weather_route(req, "regex")

    if req.ui_context:
        route_info = await laya.route(_route_state(req), req.ui_context)
        if route_info and route_info.get("about_self", 0.0) >= settings.laya_about_self_floor:
            tracing.tag(tool="identity", routed_by="laya",
                        about_self=round(route_info["about_self"], 3))
            with tracing.span("identity", tracing.TOOL) as trace_span:
                trace_span.set_inputs({"about_self": route_info["about_self"]})
                directive = _identity_directive(req)
                trace_span.set_outputs({"soul_chars": len(_soul())})
            return context, route_info, directive
        if route_info and route_info["tool"] == "weather":
            tracing.tag(tool="weather", routed_by="laya",
                        confidence=round(route_info["tool_confidence"], 3))
            return context, route_info, await _weather_route(req, "laya")
        if (route_info and route_info["tool"] and route_info["tool"] != "none"
                and route_info["tool_confidence"] >= settings.laya_confidence_floor):
            with tracing.span(route_info["tool"], tracing.TOOL) as trace_span:
                trace_span.set_inputs({"site": route_info["site"],
                                       "confidence": route_info["tool_confidence"]})
                if route_info["tool"] == "active_alerts":
                    directive = _alerts_directive(req)
                elif route_info["tool"] == "maintenance_tasks":
                    directive = _maintenance_directive(req)
                elif route_info["tool"] == "operators":
                    ops = await operators.available_operators()
                    directive = _operators_directive(req, ops)
                else:
                    context = laya.select_context(
                        req.ui_context, route_info["tool"], route_info["site"])
                trace_span.set_outputs({"directive": tracing.clip(directive)
                                        if directive else None,
                                        "context_keys": sorted(context or {})})
            logger.info(
                "agent grounding | tool=%s conf=%.3f >= floor=%.2f -> using tool context",
                route_info["tool"], route_info["tool_confidence"],
                settings.laya_confidence_floor)
        elif route_info:
            reason = ("no tool matched" if route_info["tool"] in (None, "none")
                      else "conf %.3f < floor %.2f" % (route_info["tool_confidence"],
                                                       settings.laya_confidence_floor))
            logger.info("agent grounding | tool=%s (%s) -> full ui_context fallback",
                        route_info["tool"], reason)
        if route_info:
            tracing.tag(tool=route_info["tool"], site=route_info["site"],
                        confidence=round(route_info["tool_confidence"], 3),
                        routed_by="laya",
                        below_floor=route_info["tool_confidence"]
                        < settings.laya_confidence_floor)
    if directive is None and (req.ui_context or {}).get("open_site"):
        snap = req.ui_context["open_site"]
        tracing.tag(tool="site_summary", site=snap.get("code"))
        with tracing.span("site_summary", tracing.TOOL) as trace_span:
            trace_span.set_inputs({"site": snap.get("code")})
            directive = _site_summary_directive(snap)
            trace_span.set_outputs({"directive": tracing.clip(directive)})
        logger.info("agent grounding | open_site=%s -> site summary directive",
                    snap.get("code"))
        return context, route_info, directive

    if (directive is None and context is not req.ui_context and req.ui_context
            and req.ui_context.get("active_alerts") is not None):
        context = {**context, "active_alerts": req.ui_context["active_alerts"]}
    return context, route_info, directive


def _build_payload(req: AgentChatRequest, context: dict | None,
                   site_ref: list[dict] | None, stream: bool,
                   directive: str | None = None) -> dict:
    return {
        "model": settings.agent_llm_model,
        "messages": _build_messages(req, context, site_ref, directive),
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


def _trace_request(req: AgentChatRequest) -> dict:
    return {
        "message": req.message,
        "history_turns": len(req.history or []),
        "ui_context": tracing.context_summary(req.ui_context),
    }


async def _call_llm(payload: dict) -> dict:
    with tracing.span("granite_chat", tracing.LLM) as trace_span:
        trace_span.set_inputs({"model": payload.get("model"),
                               "messages": payload.get("messages"),
                               "temperature": payload.get("temperature"),
                               "max_tokens": payload.get("max_tokens")})
        try:
            async with httpx.AsyncClient(timeout=settings.agent_http_timeout) as client:
                resp = await client.post(settings.agent_llm_url, json=payload)
                resp.raise_for_status()
                body = resp.json()
        except httpx.HTTPError as exc:
            trace_span.set_outputs({"error": str(exc)})
            raise HTTPException(
                status_code=503, detail="Assistant model unavailable.") from exc
        try:
            content = body["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError):
            content = ""
        trace_span.set_outputs({"content": content, "usage": body.get("usage")})
        return body


@router.post("/chat", response_model=AgentChatResponse)
async def chat(req: AgentChatRequest) -> AgentChatResponse:
    if not req.message.strip():
        raise HTTPException(status_code=400, detail="Empty message.")

    with tracing.span("agent_chat", tracing.AGENT) as root:
        tracing.tag(session_id=req.session_id, endpoint="chat")
        root.set_inputs(_trace_request(req))

        context, route_info, directive = await _resolve_context(req)
        site_ref = await _load_site_reference()
        payload = _build_payload(req, context, site_ref, stream=False, directive=directive)
        body = await _call_llm(payload)

        try:
            reply = (body["choices"][0]["message"]["content"] or "").strip()
        except (KeyError, IndexError, TypeError):
            reply = ""
        root.set_outputs({"reply": reply, "model": body.get("model"),
                          "route": route_info})
    return AgentChatResponse(reply=reply, model=body.get("model"), route=route_info)


def _sse(obj: dict) -> str:
    return f"data: {json.dumps(obj)}\n\n"


async def _stream_reply(req: AgentChatRequest) -> AsyncIterator[str]:
    with tracing.span("agent_chat", tracing.AGENT) as root:
        tracing.tag(session_id=req.session_id, endpoint="chat_stream")
        root.set_inputs(_trace_request(req))

        try:
            context, route_info, directive = await _resolve_context(req)
            site_ref = await _load_site_reference()
            payload = _build_payload(req, context, site_ref, stream=True,
                                     directive=directive)
        except Exception as exc:
            logger.exception("stream setup failed")
            root.set_outputs({"error": str(exc)})
            yield _sse({"error": "Assistant unavailable."})
            yield _sse({"done": True})
            return

        if route_info:
            yield _sse({"route": route_info})

        chunks: list[str] = []
        error: str | None = None
        with tracing.span("granite_chat", tracing.LLM) as llm_span:
            llm_span.set_inputs({"model": payload.get("model"),
                                 "messages": payload.get("messages"),
                                 "temperature": payload.get("temperature"),
                                 "max_tokens": payload.get("max_tokens"),
                                 "stream": True})
            try:
                async with httpx.AsyncClient(timeout=settings.agent_http_timeout) as client:
                    async with client.stream("POST", settings.agent_llm_url,
                                             json=payload) as resp:
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
                                chunks.append(delta)
                                yield _sse({"delta": delta})
            except httpx.HTTPError as exc:
                error = str(exc)
                yield _sse({"error": "Assistant model unavailable."})
            llm_span.set_outputs({"content": "".join(chunks), "error": error})

        yield _sse({"done": True})
        root.set_outputs({"reply": "".join(chunks), "route": route_info,
                          "error": error})


@router.post("/chat/stream")
async def chat_stream(req: AgentChatRequest) -> StreamingResponse:
    if not req.message.strip():
        raise HTTPException(status_code=400, detail="Empty message.")

    return StreamingResponse(
        _stream_reply(req),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
