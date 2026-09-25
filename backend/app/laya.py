"""Laya typed-decision router: picks which dashboard slice should ground the
LLM. Best-effort only — any failure returns None and the caller falls back to
grounding with the full ui_context."""
from __future__ import annotations

import httpx

from .config import settings

TOOL_CRITERIA = {
    "wind_status": "live wind speed, gusts, or whether turbines are shut down for high wind",
    "solar_forecast": "solar output, cloud cover, irradiance, or panel performance",
    "wave_forecast": "wave height, wave energy, or wave-farm output",
    "active_alerts": "current alarms, warnings, faults, or what needs attention",
    "maintenance_tasks": "scheduled work orders, inspections, repairs, or tasks to take care of",
    "operators": "which technicians or operatives are available, their skills, schedules, or who can perform a task",
    "fleet_totals": "total power generation across the whole fleet right now",
    "none": "greetings, thanks, or anything not about live fleet data",
}


def _site_criteria(ui_context: dict) -> dict:
    criteria = {"any": "no specific site, or the whole fleet"}
    for key in ("wind_sites", "solar_sites", "wave_sites"):
        for s in ui_context.get(key) or []:
            code = s.get("code")
            if code:
                criteria[code] = s.get("name") or code
    return criteria


async def route(message: str, ui_context: dict) -> dict | None:
    if not settings.laya_enabled:
        return None
    payload = {
        "state": message,
        "questions": {
            "tool": {
                "type": "choice",
                "instructions": "Which backend tool should answer this message?",
                "criteria": TOOL_CRITERIA,
            },
            "site": {
                "type": "choice",
                "instructions": "Which site is this message about?",
                "criteria": _site_criteria(ui_context or {}),
            },
        },
    }
    try:
        async with httpx.AsyncClient(timeout=settings.laya_http_timeout) as client:
            resp = await client.post(settings.laya_url, json=payload)
            resp.raise_for_status()
            answers = resp.json().get("answers", {})
    except (httpx.HTTPError, ValueError):
        return None

    tool = answers.get("tool") or {}
    site = answers.get("site") or {}
    return {
        "tool": tool.get("choice"),
        "tool_confidence": float(tool.get("answer_confidence") or 0.0),
        "site": site.get("choice"),
    }


def _filter_site(sites: list, site: str | None) -> list:
    if not site or site == "any":
        return sites
    matched = [s for s in sites if s.get("code") == site]
    return matched or sites


def select_context(ui_context: dict, tool: str | None, site: str | None) -> dict:
    ctx = ui_context or {}
    totals = ctx.get("totals_mw") or {}
    base = {"at": ctx.get("at"), "view": ctx.get("view")}

    if tool == "wind_status":
        return {**base, "wind_sites": _filter_site(ctx.get("wind_sites") or [], site),
                "totals_mw": {"wind": totals.get("wind")}}
    if tool == "solar_forecast":
        return {**base, "solar_sites": _filter_site(ctx.get("solar_sites") or [], site),
                "totals_mw": {"solar": totals.get("solar")}}
    if tool == "wave_forecast":
        return {**base, "wave_sites": _filter_site(ctx.get("wave_sites") or [], site),
                "totals_mw": {"wave": totals.get("wave")}}
    if tool == "active_alerts":
        return {**base, "active_alerts": ctx.get("active_alerts") or []}
    if tool == "maintenance_tasks":
        return {**base, "maintenance_tasks": ctx.get("maintenance_tasks") or []}
    if tool == "fleet_totals":
        return {**base, "totals_mw": totals}
    return ctx
