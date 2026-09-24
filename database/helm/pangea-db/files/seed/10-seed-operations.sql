-- ============================================================================
-- 10 - Seed: alerts, work orders, parts requirements
-- References turbines/sites/operatives/parts by their business codes so it is
-- independent of generated serial ids. Consistent with the attention set in 09.
-- ============================================================================

-- ---------------------------------------------------------------------------
-- Alerts
-- ---------------------------------------------------------------------------
INSERT INTO alerts (alert_time, severity, category, status, turbine_id, site_id, title, message, recommended_action)
VALUES
(now() - interval '2 hours',  'critical', 'sensor_anomaly', 'active',
    (SELECT turbine_id FROM turbines WHERE turbine_code = 'NSO-04'),
    (SELECT site_id FROM sites WHERE site_code = 'NSO'),
    'Turbine NSO-04 - vibration increase',
    'Main-shaft triaxial vibration trending up over the last 24h (X-axis RMS > 4.5 mm/s).',
    'Schedule bearing inspection within 10 days; verify against baseline.'),
(now() - interval '5 hours',  'critical', 'sensor_anomaly', 'active',
    (SELECT turbine_id FROM turbines WHERE turbine_code = 'MTP-07'),
    (SELECT site_id FROM sites WHERE site_code = 'MTP'),
    'Turbine MTP-07 - bearing temperature rising',
    'Process temperature elevated and correlated with vibration. Possible early bearing degradation.',
    'Inspect main bearing; check lubrication.'),
(now() - interval '4 hours',  'warning',  'ml_prediction', 'active',
    (SELECT turbine_id FROM turbines WHERE turbine_code = 'CRG-03'),
    (SELECT site_id FROM sites WHERE site_code = 'CRG'),
    'Turbine CRG-03 - output below expected',
    'Predicted power output deviates from wind-adjusted expectation. Model flags early warning.',
    'Review pitch calibration and recent SCADA trend.'),
(now() - interval '8 hours',  'warning',  'maintenance_due', 'active',
    (SELECT turbine_id FROM turbines WHERE turbine_code = 'DFL-05'),
    (SELECT site_id FROM sites WHERE site_code = 'DFL'),
    'Turbine DFL-05 - maintenance window due',
    'Scheduled preventive maintenance interval reached.',
    'Plan preventive maintenance on a low-wind day.'),
(now() - interval '1 day',    'warning',  'maintenance_due', 'acknowledged',
    (SELECT turbine_id FROM turbines WHERE turbine_code = 'MWP-11'),
    (SELECT site_id FROM sites WHERE site_code = 'MWP'),
    'Turbine MWP-11 - in maintenance',
    'Turbine taken offline for planned preventive maintenance.',
    'Complete work order and return to service.'),
(now() - interval '6 hours',  'critical', 'parts_shortage', 'active',
    NULL,
    (SELECT site_id FROM sites WHERE site_code = 'NSO'),
    'Low stock: main shaft bearing',
    'Main shaft bearing (BRG-MAIN-001) available quantity at/below reorder point with a 90-day lead time.',
    'Raise purchase order to avoid delaying corrective work.'),
(now() - interval '3 hours',  'info',     'weather_warning', 'active',
    NULL,
    (SELECT site_id FROM sites WHERE site_code = 'NSO'),
    'North Sea - high wind forecast',
    'Forecast wind above safe access limit (>15 m/s) over the next 36h.',
    'Defer offshore access; reschedule non-urgent work.');

-- ---------------------------------------------------------------------------
-- Work orders
-- ---------------------------------------------------------------------------
INSERT INTO work_orders (work_order_number, turbine_id, work_type, priority, status, title, description,
                         failure_mode, assigned_operative_id, assigned_date, scheduled_start, scheduled_end,
                         estimated_hours, predicted_failure_probability, ml_triggered, requires_low_wind, created_by)
VALUES
('WO-2026-000101',
    (SELECT turbine_id FROM turbines WHERE turbine_code = 'NSO-04'),
    'corrective', 'high', 'assigned',
    'NSO-04 main bearing inspection & likely replacement',
    'Vibration trend indicates bearing wear. Inspect and replace main shaft bearing if confirmed.',
    'OSF',
    (SELECT operative_id FROM maintenance_operatives WHERE employee_code = 'OP-001'),
    now() - interval '2 hours', now() + interval '3 days', now() + interval '3 days 6 hours',
    6.0, 0.78, TRUE, TRUE, 'pangea-ai'),
('WO-2026-000102',
    (SELECT turbine_id FROM turbines WHERE turbine_code = 'MTP-07'),
    'inspection', 'high', 'assigned',
    'MTP-07 bearing temperature investigation',
    'Investigate rising bearing temperature; verify lubrication and sensor readings.',
    NULL,
    (SELECT operative_id FROM maintenance_operatives WHERE employee_code = 'OP-004'),
    now() - interval '4 hours', now() + interval '2 days', now() + interval '2 days 4 hours',
    4.0, 0.61, TRUE, TRUE, 'pangea-ai'),
('WO-2026-000103',
    (SELECT turbine_id FROM turbines WHERE turbine_code = 'MWP-11'),
    'preventive', 'medium', 'in_progress',
    'MWP-11 scheduled gearbox oil change',
    'Routine gearbox lubrication oil replacement and filter change.',
    NULL,
    (SELECT operative_id FROM maintenance_operatives WHERE employee_code = 'OP-008'),
    now() - interval '1 day', now() - interval '2 hours', now() + interval '3 hours',
    5.0, NULL, FALSE, FALSE, 'supervisor'),
('WO-2026-000104',
    (SELECT turbine_id FROM turbines WHERE turbine_code = 'DFL-05'),
    'preventive', 'low', 'scheduled',
    'DFL-05 preventive maintenance',
    'Scheduled preventive maintenance at interval.',
    NULL,
    (SELECT operative_id FROM maintenance_operatives WHERE employee_code = 'OP-011'),
    NULL, now() + interval '5 days', now() + interval '5 days 5 hours',
    5.0, NULL, FALSE, TRUE, 'supervisor'),
('WO-2026-000105',
    (SELECT turbine_id FROM turbines WHERE turbine_code = 'CRG-03'),
    'corrective', 'medium', 'scheduled',
    'CRG-03 pitch calibration',
    'Investigate output shortfall; recalibrate blade pitch.',
    'PWF',
    (SELECT operative_id FROM maintenance_operatives WHERE employee_code = 'OP-005'),
    NULL, now() + interval '4 days', now() + interval '4 days 3 hours',
    3.0, 0.44, TRUE, TRUE, 'pangea-ai'),
('WO-2026-000106',
    (SELECT turbine_id FROM turbines WHERE turbine_code = 'NSO-19'),
    'preventive', 'medium', 'completed',
    'NSO-19 blade inspection',
    'Completed routine blade inspection; no defects found.',
    NULL,
    (SELECT operative_id FROM maintenance_operatives WHERE employee_code = 'OP-009'),
    now() - interval '9 days', now() - interval '7 days', now() - interval '7 days' + interval '4 hours',
    4.0, NULL, FALSE, TRUE, 'supervisor');

-- ---------------------------------------------------------------------------
-- Parts requirements (parts needed per work order)
-- ---------------------------------------------------------------------------
INSERT INTO parts_requirements (work_order_id, part_id, quantity_required, quantity_allocated, notes)
VALUES
((SELECT work_order_id FROM work_orders WHERE work_order_number = 'WO-2026-000101'),
 (SELECT part_id FROM parts_inventory WHERE part_number = 'BRG-MAIN-001'), 1, 1, 'Reserve main bearing for corrective work.'),
((SELECT work_order_id FROM work_orders WHERE work_order_number = 'WO-2026-000102'),
 (SELECT part_id FROM parts_inventory WHERE part_number = 'SNS-VIB-003'), 2, 2, 'Replace suspect vibration sensors.'),
((SELECT work_order_id FROM work_orders WHERE work_order_number = 'WO-2026-000103'),
 (SELECT part_id FROM parts_inventory WHERE part_number = 'LUB-OIL-031'), 2, 2, 'Gearbox oil.'),
((SELECT work_order_id FROM work_orders WHERE work_order_number = 'WO-2026-000103'),
 (SELECT part_id FROM parts_inventory WHERE part_number = 'GBX-SEAL-014'), 1, 1, 'Seal kit for oil change.');
