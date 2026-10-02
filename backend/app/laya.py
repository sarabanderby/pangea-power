from __future__ import annotations

import json
import logging

import httpx

from . import tracing
from .config import settings

logger = logging.getLogger("pangea.laya")

TOOL_CRITERIA = {
    "wind_status": "anything about wind sites or turbines: live wind speed, gusts, whether a site is above the cut-out wind speed, or whether turbines are shut down",
    "solar_forecast": "anything about solar sites or panels: solar output, how much a solar site is producing, cloud cover, irradiance, panel performance, or why a solar site is underperforming",
    "wave_forecast": "anything about wave farms: wave height, wave energy, or how much a wave farm is generating",
    "active_alerts": "current alarms, warnings, faults, or what needs attention right now",
    "maintenance_tasks": "scheduled work orders, inspections, repairs, tasks to take care of, or the condition of site infrastructure such as drainage, access roads or foundations",
    "operators": "which technicians or operatives are available, their skills, certifications, training, schedules, whether a crew has finished a job, or who can perform a task",
    "fleet_totals": "combined generation across all energy types at once; only when no single technology such as wind, solar or wave is named",
    "weather": "weather, temperature, rain or snow at a town, city or country anywhere in the world that is not one of the fleet's own sites",
    "none": "greetings, thanks, sign-offs such as 'that's all', or anything that is not a question about the fleet or the weather",
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
            "about_self": {
                "type": "noul",
                "instructions": "Is the user asking about you, the assistant "
                                "itself — what you are, what you can do, how you "
                                "work — rather than about the energy fleet?",
            },
        },
    }
    with tracing.span("laya_route", tracing.CHAT_MODEL) as trace_span:
        trace_span.set_inputs({"state": message,
                               "tools": list(TOOL_CRITERIA),
                               "sites": list(payload["questions"]["site"]["criteria"])})
        try:
            async with httpx.AsyncClient(timeout=settings.laya_http_timeout) as client:
                resp = await client.post(settings.laya_url, json=payload)
                resp.raise_for_status()
                raw = resp.json()
                answers = raw.get("answers", {})
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("laya route failed for %r: %s", message, exc)
            trace_span.set_outputs({"error": str(exc)})
            return None

        tool = answers.get("tool") or {}
        site = answers.get("site") or {}
        about_self = answers.get("about_self") or {}
        decision = {
            "tool": tool.get("choice"),
            "tool_confidence": float(tool.get("answer_confidence") or 0.0),
            "site": site.get("choice"),
            "about_self": float(about_self.get("noul") or 0.0),
        }
        trace_span.set_outputs({**decision, "answers": answers})

    logger.info(
        "laya route | msg=%r -> tool=%s conf=%.3f site=%s about_self=%.3f | raw_answers=%s",
        message, decision["tool"], decision["tool_confidence"], decision["site"],
        decision["about_self"], json.dumps(answers, default=str),
    )
    return decision


def _filter_site(sites: list, site: str | None) -> list:
    if not site or site == "any":
        return sites
    matched = [s for s in sites if s.get("code") == site]
    return matched or sites


def select_context(ui_context: dict, tool: str | None, site: str | None) -> dict:
    ctx = ui_context or {}
    totals = ctx.get("totals_mw") or {}
    base = {"at": ctx.get("at"), "view": ctx.get("view")}
    if ctx.get("open_site"):
        base["open_site"] = ctx["open_site"]

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
