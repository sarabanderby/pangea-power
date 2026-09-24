-- ============================================================================
-- 09 - Seed: time-series (last 48h) for sensor_readings, wind_data, ml_predictions
-- Turbines in the "attention" set below get an upward vibration / anomaly /
-- failure-probability trend so the predictive panels have something to show.
-- ============================================================================
SELECT setseed(0.42);

-- Attention turbines (kept consistent with alerts/work-orders in seed 10):
--   NSO-04, MTP-07, CRG-03, DFL-05, MWP-11

-- ---------------------------------------------------------------------------
-- Sensor readings: hourly for the last 48 hours, per turbine
-- ---------------------------------------------------------------------------
INSERT INTO sensor_readings (
    reading_time, turbine_id, air_temperature_c, process_temperature_c,
    rotational_speed_rpm, torque_nm, tool_wear_minutes,
    vibration_x_mms, vibration_y_mms, vibration_z_mms,
    power_output_kw, wind_speed_ms, wind_direction_deg,
    nacelle_position_deg, pitch_angle_deg, anomaly_score
)
SELECT
    ts,
    t.turbine_id,
    round((14.85 + random() * 10)::numeric, 2),
    round((34.85 + random() * 15 + deg.f * 8)::numeric, 2),
    CASE WHEN t.status = 'operational' THEN (900 + (w.wind * 40)::int + (random() * 120)::int) ELSE 0 END,
    round((CASE WHEN t.status = 'operational' THEN 20 + random() * 35 ELSE 0 END)::numeric, 2),
    (random() * 220)::int,
    round((0.8 + random() * 1.4 + deg.f * 4.0)::numeric, 3),
    round((0.7 + random() * 1.2 + deg.f * 3.0)::numeric, 3),
    round((0.6 + random() * 1.0 + deg.f * 2.0)::numeric, 3),
    round(p.power::numeric, 2),
    w.wind,
    (random() * 359)::int,
    (random() * 359)::int,
    round((random() * 8)::numeric, 2),
    least(1.0, round((random() * 0.15 + deg.f * 0.8)::numeric, 4))
FROM turbines t
CROSS JOIN LATERAL generate_series(
        date_trunc('hour', now()) - interval '47 hours',
        date_trunc('hour', now()),
        interval '1 hour') AS ts
CROSS JOIN LATERAL (
    SELECT greatest(0.037,
        (extract(epoch FROM ts) - extract(epoch FROM (date_trunc('hour', now()) - interval '47 hours')))
        / (47 * 3600.0)) AS prog
) pr
CROSS JOIN LATERAL (
    SELECT CASE WHEN t.turbine_code IN ('NSO-04','MTP-07','CRG-03','DFL-05','MWP-11')
                THEN pr.prog ELSE 0 END AS f
) deg
CROSS JOIN LATERAL (
    SELECT greatest(0.5, round((6 + 5 * random() + 3 * sin(extract(epoch FROM ts) / 9000.0))::numeric, 2)) AS wind
) w
CROSS JOIN LATERAL (
    SELECT CASE WHEN t.status = 'operational'
                THEN least(t.rated_power_kw, t.rated_power_kw * greatest(0, least(1, (w.wind - 3) / 9.0)))
                ELSE 0 END AS power
) p;

-- ---------------------------------------------------------------------------
-- Wind data: hourly for the last 48 hours, per site
-- ---------------------------------------------------------------------------
INSERT INTO wind_data (
    measurement_time, site_id, wind_speed_ms, wind_gust_ms, wind_direction_deg,
    air_temperature_c, humidity_percent, pressure_hpa, precipitation_mm,
    visibility_km, data_source
)
SELECT
    ts,
    s.site_id,
    w.wind,
    round((w.wind + random() * 4)::numeric, 2),
    (random() * 359)::int,
    round((s.min_temp_celsius + random() * (s.max_temp_celsius - s.min_temp_celsius))::numeric, 2),
    (40 + random() * 55)::int,
    round((995 + random() * 30)::numeric, 2),
    round((random() * 3)::numeric, 2),
    round((5 + random() * 35)::numeric, 2),
    'simulator'
FROM sites s
CROSS JOIN LATERAL generate_series(
        date_trunc('hour', now()) - interval '47 hours',
        date_trunc('hour', now()),
        interval '1 hour') AS ts
CROSS JOIN LATERAL (
    SELECT greatest(0.5,
        round((s.avg_wind_speed_ms + 4 * sin(extract(epoch FROM ts) / 9000.0) + random() * 3 - 1.5)::numeric, 2)) AS wind
) w;

-- ---------------------------------------------------------------------------
-- ML predictions: every 6 hours for the last 48 hours, per turbine
-- ---------------------------------------------------------------------------
INSERT INTO ml_predictions (
    prediction_time, turbine_id, model_name, model_version,
    input_air_temp_c, input_process_temp_c, input_rpm, input_torque_nm,
    input_tool_wear_min, input_model_type,
    failure_predicted, failure_probability, time_to_failure_hours,
    predicted_failure_mode, confidence_score, inference_latency_ms
)
SELECT
    ts,
    t.turbine_id,
    'pangea-pdm', 'v0.1',
    round((16.85 + random() * 8)::numeric, 2),
    round((36.85 + random() * 12 + deg.f * 8)::numeric, 2),
    (1000 + random() * 600)::int,
    round((25 + random() * 30)::numeric, 2),
    (random() * 220)::int,
    CASE t.model WHEN 'L' THEN 0 WHEN 'M' THEN 1 ELSE 2 END,
    (prob.p > 0.5),
    round(prob.p::numeric, 4),
    CASE WHEN prob.p > 0.5 THEN (24 + (random() * 400)::int) ELSE NULL END,
    CASE WHEN prob.p > 0.5 THEN (ARRAY['HDF','PWF','OSF','TWF'])[(1 + floor(random() * 4))::int] ELSE NULL END,
    round((0.70 + random() * 0.30)::numeric, 4),
    (8 + random() * 40)::int
FROM turbines t
CROSS JOIN LATERAL generate_series(
        date_trunc('hour', now()) - interval '48 hours',
        date_trunc('hour', now()),
        interval '6 hours') AS ts
CROSS JOIN LATERAL (
    SELECT greatest(0.02,
        (extract(epoch FROM ts) - extract(epoch FROM (date_trunc('hour', now()) - interval '48 hours')))
        / (48 * 3600.0)) AS prog
) pr
CROSS JOIN LATERAL (
    SELECT CASE WHEN t.turbine_code IN ('NSO-04','MTP-07','CRG-03','DFL-05','MWP-11')
                THEN pr.prog ELSE 0 END AS f
) deg
CROSS JOIN LATERAL (
    SELECT least(0.97, CASE WHEN deg.f > 0 THEN 0.35 + deg.f * 0.55 ELSE random() * 0.14 END) AS p
) prob;

-- Populate the rollup materialized views from the freshly seeded data.
SELECT refresh_rollups();
