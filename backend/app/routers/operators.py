"""Operative availability: skills, weekly schedule, upcoming work, and load."""
import json
from datetime import date

from fastapi import APIRouter, Query

from .. import db
from ..models import OperatorAvailability

router = APIRouter(prefix="/api/operators", tags=["operators"])

_OPEN_STATUSES = ("scheduled", "assigned", "in_progress", "on_hold")
_DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]

_QUERY = (
    "SELECT o.operative_id, o.employee_code, "
    "       o.first_name || ' ' || o.last_name AS full_name, "
    "       o.skill_level::text AS skill_level, "
    "       o.certifications::text[] AS certifications, o.specializations, "
    "       o.base_location, o.offshore_certified, o.max_travel_distance_km, "
    "       o.on_leave_until, "
    "       s.monday::text AS monday, s.tuesday::text AS tuesday, "
    "       s.wednesday::text AS wednesday, s.thursday::text AS thursday, "
    "       s.friday::text AS friday, s.saturday::text AS saturday, "
    "       s.sunday::text AS sunday, s.exception_dates, "
    "       (SELECT count(*) FROM work_orders w "
    "          WHERE w.assigned_operative_id = o.operative_id "
    "            AND w.status = ANY($1::work_order_status[])) AS open_assignments, "
    "       (SELECT w.title FROM work_orders w "
    "          WHERE w.assigned_operative_id = o.operative_id "
    "            AND w.status = ANY($1::work_order_status[]) "
    "            AND w.status = 'in_progress' "
    "          ORDER BY w.scheduled_start NULLS LAST LIMIT 1) AS current_task, "
    "       (SELECT json_agg(json_build_object("
    "                 'title', w.title, 'status', w.status::text, "
    "                 'scheduled_start', w.scheduled_start, "
    "                 'scheduled_end', w.scheduled_end) ORDER BY w.scheduled_start) "
    "          FROM work_orders w "
    "          WHERE w.assigned_operative_id = o.operative_id "
    "            AND w.status = ANY($1::work_order_status[]) "
    "            AND w.scheduled_start >= CURRENT_DATE "
    "            AND w.scheduled_start < CURRENT_DATE + make_interval(days => $2)) AS upcoming "
    "FROM maintenance_operatives o "
    "LEFT JOIN LATERAL ("
    "  SELECT * FROM operative_schedules "
    "  WHERE operative_id = o.operative_id AND effective_date <= CURRENT_DATE "
    "    AND (end_date IS NULL OR end_date >= CURRENT_DATE) "
    "  ORDER BY effective_date DESC LIMIT 1"
    ") s ON TRUE "
    "WHERE o.is_active = TRUE "
    "  AND (o.on_leave_until IS NULL OR o.on_leave_until < CURRENT_DATE) "
    "ORDER BY o.skill_level DESC, full_name"
)


async def available_operators(horizon_days: int = 14) -> list[OperatorAvailability]:
    rows = await db.fetch(_QUERY, list(_OPEN_STATUSES), horizon_days)
    today = _DAYS[date.today().weekday()]
    result = []
    for r in rows:
        weekly = {d: r[d] for d in _DAYS}
        raw = r["upcoming"]
        upcoming = json.loads(raw) if isinstance(raw, str) else (raw or [])
        result.append(OperatorAvailability(
            employee_code=r["employee_code"],
            name=r["full_name"],
            skill_level=r["skill_level"],
            certifications=list(r["certifications"] or []),
            specializations=list(r["specializations"] or []),
            base_location=r["base_location"],
            offshore_certified=r["offshore_certified"],
            max_travel_distance_km=r["max_travel_distance_km"],
            today_shift=weekly[today],
            weekly_schedule=weekly,
            exception_dates=list(r["exception_dates"] or []),
            on_leave_until=r["on_leave_until"],
            open_assignments=r["open_assignments"],
            current_task=r["current_task"],
            upcoming_assignments=upcoming,
        ))
    return result


@router.get("", response_model=list[OperatorAvailability])
async def list_operators(
    horizon_days: int = Query(14, ge=1, le=60),
) -> list[OperatorAvailability]:
    return await available_operators(horizon_days)
