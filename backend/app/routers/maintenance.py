"""Maintenance work orders: read the open feed and create/assign new tasks."""
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query

from .. import db
from ..models import WorkOrder, WorkOrderCreate

router = APIRouter(prefix="/api/maintenance", tags=["maintenance"])

_OPEN_STATUSES = ("scheduled", "assigned", "in_progress", "on_hold")
_WORK_TYPES = {"preventive", "corrective", "inspection", "emergency"}
_PRIORITIES = {"low", "medium", "high", "critical"}


@router.get("", response_model=list[WorkOrder])
async def list_work_orders(
    limit: int = Query(50, ge=1, le=200),
) -> list[WorkOrder]:
    rows = await db.fetch(
        "SELECT w.work_order_number, w.title, w.work_type::text AS work_type, "
        "       w.priority::text AS priority, w.status::text AS status, "
        "       t.turbine_code, s.site_name, w.scheduled_start "
        "FROM work_orders w "
        "LEFT JOIN turbines t ON t.turbine_id = w.turbine_id "
        "LEFT JOIN sites s ON s.site_id = t.site_id "
        "WHERE w.status = ANY($1::work_order_status[]) "
        "ORDER BY CASE w.priority "
        "           WHEN 'critical' THEN 0 WHEN 'high' THEN 1 "
        "           WHEN 'medium' THEN 2 ELSE 3 END, "
        "         w.scheduled_start NULLS LAST "
        "LIMIT $2",
        list(_OPEN_STATUSES),
        limit,
    )
    return [WorkOrder(**dict(r)) for r in rows]


@router.post("", response_model=WorkOrder, status_code=201)
async def create_work_order(req: WorkOrderCreate) -> WorkOrder:
    if req.work_type not in _WORK_TYPES:
        raise HTTPException(400, f"Invalid work_type; expected one of {sorted(_WORK_TYPES)}")
    if req.priority not in _PRIORITIES:
        raise HTTPException(400, f"Invalid priority; expected one of {sorted(_PRIORITIES)}")

    turb = await db.fetchrow(
        "SELECT turbine_id FROM turbines WHERE turbine_code = $1", req.turbine_code)
    if not turb:
        raise HTTPException(400, f"Unknown turbine_code {req.turbine_code!r}")

    op_id = None
    if req.assigned_operative_code:
        op = await db.fetchrow(
            "SELECT operative_id FROM maintenance_operatives WHERE employee_code = $1",
            req.assigned_operative_code)
        if not op:
            raise HTTPException(400, f"Unknown operative {req.assigned_operative_code!r}")
        op_id = op["operative_id"]

    year = datetime.now().year
    seq = await db.fetchrow(
        "SELECT COALESCE(MAX(SUBSTRING(work_order_number FROM 9)::int), 0) + 1 AS n "
        "FROM work_orders WHERE work_order_number ~ $1",
        f"^WO-{year}-[0-9]+$")
    wo_number = f"WO-{year}-{seq['n']:06d}"
    status = "assigned" if op_id else "scheduled"

    row = await db.fetchrow(
        "INSERT INTO work_orders "
        "  (work_order_number, turbine_id, work_type, priority, status, title, description, "
        "   assigned_operative_id, assigned_date, scheduled_start, scheduled_end, "
        "   estimated_hours, created_by) "
        "VALUES ($1, $2, $3::work_order_type, $4::work_order_priority, "
        "        $5::work_order_status, $6, $7, $8, "
        "        CASE WHEN $8::int IS NULL THEN NULL ELSE now() END, $9, $10, $11, 'schedule-ui') "
        "RETURNING work_order_number, title, work_type::text AS work_type, "
        "          priority::text AS priority, status::text AS status, scheduled_start",
        wo_number, turb["turbine_id"], req.work_type, req.priority, status,
        req.title, req.description, op_id, req.scheduled_start, req.scheduled_end,
        req.estimated_hours)

    site = await db.fetchrow(
        "SELECT s.site_name FROM turbines t JOIN sites s ON s.site_id = t.site_id "
        "WHERE t.turbine_id = $1", turb["turbine_id"])
    return WorkOrder(
        turbine_code=req.turbine_code,
        site_name=site["site_name"] if site else None,
        **dict(row),
    )
