"""MLflow evaluation of the Pangea agent's tool-routing accuracy.

The Pangea agent (backend/app/routers/agent.py) doesn't do OpenAI-style function
calling. Instead, for every chat request that carries a `ui_context` (the
dashboard snapshot), it asks the "Laya" typed-decision router
(backend/app/laya.py) which backend "tool" should ground the reply -- one of
wind_status, solar_forecast, wave_forecast, active_alerts, maintenance_tasks,
operators, fleet_totals, or none -- plus which site it's about. That decision
is returned as `route: {tool, tool_confidence, site}` in the /api/agent/chat
response and *is* the agent's tool call in this system.

This script builds a small hand-labeled dataset of (message, ui_context) ->
expected (tool, site), calls the live deployed agent for each case, records
the routing decision as an MLflow TOOL span, and scores it with MLflow's
built-in ToolCallCorrectness scorer in exact-match mode (deterministic, no
judge model / API key required).

Usage:
    python eval/eval_agent_tool_calls.py
    PANGEA_AGENT_URL=http://localhost:8000 python eval/eval_agent_tool_calls.py

Env vars:
    PANGEA_AGENT_URL     Base URL of the deployed backend (default: prod route below)
    MLFLOW_TRACKING_URI  Where to log results (default: local ./mlruns file store)
    MLFLOW_EXPERIMENT    MLflow experiment name (default: pangea-agent-tool-routing)
"""
from __future__ import annotations

import os
import sys

import mlflow
import requests
from mlflow.entities import SpanType
from mlflow.genai.scorers import ToolCallCorrectness

DEFAULT_AGENT_URL = (
    "https://pangea-api-pangea-energy-and-power.apps.caiprod.rhoai.rh-aiservices-bu.com"
)
AGENT_URL = os.environ.get("PANGEA_AGENT_URL", DEFAULT_AGENT_URL).rstrip("/")
REQUEST_TIMEOUT_S = 60

# ---------------------------------------------------------------------------
# Shared dashboard snapshot, shaped like frontend/dashboard-app.html's
# buildUiContext(), populated with the real site codes/names from
# database/helm/pangea-db/files/seed/07-seed-sites.sql.
# ---------------------------------------------------------------------------
UI_CONTEXT = {
    "view": "dashboard",
    "open_forecast": False,
    "totals_mw": {"wind": 142.3, "solar": 58.7, "wave": 9.4, "total": 210.4},
    "wind_sites": [
        {"code": "MTP", "name": "Mountain Peak", "status": "operational",
         "wind_ms": 9.8, "gust_ms": 14.2, "cutout_ms": 22.0, "shutdown": False},
        {"code": "NSO", "name": "North Sea Offshore", "status": "shutdown",
         "wind_ms": 23.5, "gust_ms": 29.1, "cutout_ms": 22.0, "shutdown": True},
        {"code": "MWP", "name": "Baltic Plains", "status": "operational",
         "wind_ms": 7.1, "gust_ms": 10.4, "cutout_ms": 22.0, "shutdown": False},
        {"code": "CRG", "name": "Coastal Ridge", "status": "operational",
         "wind_ms": 8.9, "gust_ms": 12.7, "cutout_ms": 22.0, "shutdown": False},
        {"code": "DFL", "name": "Iberian Flats", "status": "operational",
         "wind_ms": 6.2, "gust_ms": 9.0, "cutout_ms": 22.0, "shutdown": False},
        {"code": "BJF", "name": "Bjornefjall Wind Farm", "status": "operational",
         "wind_ms": 10.6, "gust_ms": 16.8, "cutout_ms": 22.0, "shutdown": False},
    ],
    "solar_sites": [
        {"code": "GDQ", "name": "Guadalquivir Solar", "status": "operational",
         "predicted_kw": 41000, "actual_kw": 39500, "residual_pct": -3.7,
         "cloud_cover_pct": 12.0, "irradiance_wm2": 780},
        {"code": "ALT", "name": "Alentejo Solar", "status": "operational",
         "predicted_kw": 33000, "actual_kw": 32100, "residual_pct": -2.7,
         "cloud_cover_pct": 18.0, "irradiance_wm2": 710},
        {"code": "TRN", "name": "Trinacria Solar", "status": "underperforming",
         "predicted_kw": 29000, "actual_kw": 21000, "residual_pct": -27.6,
         "cloud_cover_pct": 64.0, "irradiance_wm2": 340},
    ],
    "wave_sites": [
        {"code": "ORK", "name": "Orkney Wave Farm", "status": "operational",
         "predicted_kw": 5200, "actual_kw": 4950, "residual_pct": -4.8},
        {"code": "RUN", "name": "Runde Wave Farm", "status": "operational",
         "predicted_kw": 4100, "actual_kw": 4300, "residual_pct": 4.9},
    ],
    "active_alerts": [
        {"title": "High wind shutdown", "severity": "warning",
         "turbine": "NSO-07", "site": "North Sea Offshore"},
        {"title": "Solar underperformance", "severity": "critical",
         "turbine": None, "site": "Trinacria Solar"},
    ],
    "maintenance_tasks": [
        {"id": "WO-1042", "title": "Gearbox oil seal replacement", "type": "repair",
         "priority": "high", "status": "scheduled", "turbine": "MTP-03",
         "site": "Mountain Peak", "scheduled_start": "2026-09-26T08:00:00Z"},
        {"id": "WO-1055", "title": "Quarterly blade inspection", "type": "inspection",
         "priority": "medium", "status": "scheduled", "turbine": "CRG-11",
         "site": "Coastal Ridge", "scheduled_start": "2026-09-29T09:00:00Z"},
    ],
    "at": "2026-09-25T12:00:00Z",
}


def _case(message: str, tool: str, site: str) -> dict:
    return {
        "inputs": {"message": message, "ui_context": UI_CONTEXT},
        "expectations": {
            "expected_tool_calls": [{"name": tool, "arguments": {"site": site}}],
        },
    }


DATASET = [
    _case("what's the wind speed at Bjornefjall right now?", "wind_status", "BJF"),
    _case("are the North Sea turbines shut down for high wind?", "wind_status", "NSO"),

    _case("what's the cloud cover forecast for Guadalquivir?", "solar_forecast", "GDQ"),
    _case("how's solar output looking today?", "solar_forecast", "any"),

    _case("what's the wave height at Orkney?", "wave_forecast", "ORK"),
    _case("how much is the wave farm generating?", "wave_forecast", "any"),

    _case("what alarms are currently active?", "active_alerts", "any"),
    _case("any critical faults I need to know about?", "active_alerts", "any"),

    _case("what maintenance is scheduled this week?", "maintenance_tasks", "any"),
    _case("any repairs due at Mountain Peak?", "maintenance_tasks", "MTP"),

    _case("which technicians are available today?", "operators", "any"),
    _case("who can do offshore work at North Sea?", "operators", "NSO"),

    _case("how much are we generating across the whole fleet right now?", "fleet_totals", "any"),
    _case("what's our total power output?", "fleet_totals", "any"),

    _case("thanks, that's all", "none", "any"),
    _case("good morning!", "none", "any"),
]


def predict_fn(message: str, ui_context: dict) -> dict:
    """Call the live agent and record its routing decision as a TOOL span.

    The outer span is the trace root: it carries the real request (message,
    ui_context) and response (reply, route) so the trace reads like a normal
    agent call. The Laya routing decision is recorded as a nested TOOL span
    -- without this wrapper, mlflow.start_span() below would itself become
    the (parentless) trace root, and the trace would show only the tool call
    with no visible request/reply.
    """
    with mlflow.start_span(name="agent_chat", span_type=SpanType.AGENT) as root:
        root.set_inputs({"message": message, "ui_context": ui_context})

        resp = requests.post(
            f"{AGENT_URL}/api/agent/chat",
            json={"message": message, "ui_context": ui_context, "history": []},
            timeout=REQUEST_TIMEOUT_S,
        )
        resp.raise_for_status()
        body = resp.json()

        route = body.get("route") or {}
        tool_name = route.get("tool") or "none"
        site = route.get("site")

        with mlflow.start_span(name=tool_name, span_type=SpanType.TOOL) as tool_span:
            tool_span.set_inputs({"site": site})
            tool_span.set_outputs({"tool_confidence": route.get("tool_confidence")})

        output = {"reply": body.get("reply"), "route": route}
        root.set_outputs(output)
        return output


def main() -> None:
    if tracking_uri := os.environ.get("MLFLOW_TRACKING_URI"):
        mlflow.set_tracking_uri(tracking_uri)
    mlflow.set_experiment(os.environ.get("MLFLOW_EXPERIMENT", "pangea-agent-tool-routing"))

    # The deployed Laya router is a single small KServe pod; evaluating with
    # MLflow's default concurrency (10 parallel predict_fn calls) overwhelms
    # it and most requests time out (laya_http_timeout=5s server-side),
    # silently falling back to an empty route. Keep concurrency low unless
    # the caller overrides it.
    os.environ.setdefault("MLFLOW_GENAI_EVAL_MAX_WORKERS", "3")

    try:
        health = requests.get(f"{AGENT_URL}/api/health", timeout=10)
        health.raise_for_status()
    except requests.RequestException as exc:
        print(
            f"WARNING: could not reach {AGENT_URL}/api/health ({exc}).\n"
            "The agent endpoint may need cluster VPN/auth, or be unreachable from "
            "this machine. Set PANGEA_AGENT_URL to a reachable instance (e.g. a "
            "port-forwarded route) and retry.",
            file=sys.stderr,
        )

    result = mlflow.genai.evaluate(
        data=DATASET,
        predict_fn=predict_fn,
        scorers=[ToolCallCorrectness(should_exact_match=True)],
    )

    print("\n=== Aggregate metrics ===")
    for name, value in result.metrics.items():
        print(f"{name}: {value}")

    eval_table = result.tables.get("eval_results")
    if eval_table is not None:
        out_path = os.path.join(os.path.dirname(__file__), "eval_results.csv")
        eval_table.to_csv(out_path, index=False)
        print(f"\nPer-case results written to {out_path}")

    print(f"\nMLflow run id: {result.run_id}")
    print("Run `mlflow ui` to inspect traces and per-case assessments.")


if __name__ == "__main__":
    main()
