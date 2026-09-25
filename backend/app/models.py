"""Pydantic v2 response shapes. These are what the dashboard UI consumes —
the UI never touches Postgres directly."""
from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel


class StatusCount(BaseModel):
    status: str
    count: int


class FleetStatus(BaseModel):
    total_turbines: int
    by_status: list[StatusCount]
    live_output_mw: float
    rated_capacity_mw: float


class Turbine(BaseModel):
    turbine_code: str
    site_name: str
    model: str
    status: str
    current_power_output_kw: float
    rated_power_kw: int


class SensorReading(BaseModel):
    reading_time: datetime
    power_output_kw: float | None = None
    wind_speed_ms: float | None = None
    rotational_speed_rpm: int | None = None
    torque_nm: float | None = None
    air_temperature_c: float | None = None
    process_temperature_c: float | None = None
    vibration_x_mms: float | None = None
    anomaly_score: float | None = None


class Alert(BaseModel):
    alert_id: int
    alert_time: datetime
    severity: str
    category: str
    status: str
    turbine_code: str | None = None
    site_name: str | None = None
    title: str
    message: str
    recommended_action: str | None = None


class WorkOrder(BaseModel):
    work_order_number: str
    title: str
    work_type: str
    priority: str
    status: str
    turbine_code: str | None = None
    site_name: str | None = None
    scheduled_start: datetime | None = None


class WorkOrderCreate(BaseModel):
    turbine_code: str
    work_type: str
    priority: str
    title: str
    description: str | None = None
    assigned_operative_code: str | None = None
    scheduled_start: datetime | None = None
    scheduled_end: datetime | None = None
    estimated_hours: float | None = None


class OperatorAvailability(BaseModel):
    employee_code: str
    name: str
    skill_level: str
    certifications: list[str] = []
    specializations: list[str] = []
    base_location: str | None = None
    offshore_certified: bool = False
    max_travel_distance_km: int | None = None
    today_shift: str
    weekly_schedule: dict[str, str] = {}
    exception_dates: list[date] = []
    on_leave_until: date | None = None
    open_assignments: int = 0
    current_task: str | None = None
    upcoming_assignments: list[dict] = []


class Site(BaseModel):
    site_code: str
    site_name: str
    terrain: str
    energy_type: str = "wind"
    turbine_count: int
    capacity_kw: int | None = None
    avg_wind_speed_ms: float | None = None
    latitude: float | None = None
    longitude: float | None = None
    nearest_city: str | None = None
    operational: int = 0
    maintenance: int = 0
    offline: int = 0
    live_output_mw: float = 0.0


class SolarForecastPoint(BaseModel):
    time: datetime
    predicted_kw: float


class SolarDailyForecast(BaseModel):
    date: str            # local ISO date (YYYY-MM-DD)
    peak_kw: float       # highest predicted output that day
    energy_kwh: float    # predicted generation for the day (sum of hourly kW)


class SolarForecastDetail(BaseModel):
    site_code: str
    site_name: str
    nearest_city: str | None = None
    capacity_kw: int | None = None
    weather_source: str              # 'open-meteo' | 'clear-sky-sim'
    hourly: list[SolarForecastPoint] # full multi-day hourly curve
    daily: list[SolarDailyForecast]  # per-day rollup


class WaveForecastPoint(BaseModel):
    time: datetime
    predicted_kw: float


class WaveDailyForecast(BaseModel):
    date: str            # local ISO date (YYYY-MM-DD)
    peak_kw: float       # highest predicted output that day
    energy_kwh: float    # predicted generation for the day (sum of hourly kW)


class WaveForecastDetail(BaseModel):
    site_code: str
    site_name: str
    nearest_city: str | None = None
    capacity_kw: int | None = None
    weather_source: str              # 'open-meteo-marine' | 'calm-sea-sim'
    hourly: list[WaveForecastPoint]  # full multi-day hourly curve
    daily: list[WaveDailyForecast]   # per-day rollup


class WavePrediction(BaseModel):
    site_code: str
    site_name: str
    nearest_city: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    capacity_kw: int | None = None
    # Current hour.
    predicted_now_kw: float          # physics model from live sea state
    actual_now_kw: float             # simulated SCADA reading (stand-in for a real feed)
    residual_kw: float               # predicted - actual
    residual_pct: float              # residual as % of predicted
    status: str                      # 'ok' | 'underperforming' | 'offline'
    # Context.
    wave_height_m: float             # significant wave height Hs
    wave_period_s: float             # wave (energy) period Te
    wave_power_kw_per_m: float       # wave energy flux P = 0.49 * Hs^2 * Te
    weather_source: str              # 'open-meteo-marine' | 'calm-sea-sim'
    forecast_24h: list[WaveForecastPoint]


class SolarPrediction(BaseModel):
    site_code: str
    site_name: str
    nearest_city: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    capacity_kw: int | None = None
    # Current hour.
    predicted_now_kw: float          # model expectation from live weather
    actual_now_kw: float             # simulated SCADA reading (stand-in for a real feed)
    residual_kw: float               # predicted - actual
    residual_pct: float              # residual as % of predicted
    status: str                      # 'ok' | 'underperforming' | 'offline'
    # Context.
    irradiance_wm2: float
    ambient_temp_c: float
    cloud_cover_pct: float
    weather_source: str              # 'open-meteo' | 'clear-sky-sim'
    forecast_24h: list[SolarForecastPoint]


class WindStatus(BaseModel):
    site_code: str
    site_name: str
    nearest_city: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    turbine_count: int = 0
    wind_speed_ms: float              # live hub-height (100 m) wind speed
    gust_ms: float | None = None      # 10 m gusts, when available
    cutout_ms: float                  # storm cut-out threshold
    shutdown: bool                    # wind >= cut-out -> turbines protected/stopped
    status: str                       # 'operating' | 'shutdown' | 'calm'
    weather_source: str              # 'open-meteo' | 'site-average'


class SpeakRequest(BaseModel):
    text: str
    voice: str | None = None


class AgentChatRequest(BaseModel):
    message: str
    ui_context: dict | None = None   # current dashboard state, for grounding
    history: list[dict] | None = None  # prior [{role, content}] turns


class AgentChatResponse(BaseModel):
    reply: str
    model: str | None = None
    route: dict | None = None  # laya tool/site decision, when used
