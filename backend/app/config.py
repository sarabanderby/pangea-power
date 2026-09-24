"""Application configuration.

All values come from the environment so nothing sensitive is baked into the image.
On OpenShift the DB credentials are injected from the `pangea-db-credentials`
Secret (see the Helm chart / deployment env mapping); locally they can come from
a port-forwarded database and a `.env` file.
"""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- Database ---------------------------------------------------------
    # In-cluster defaults match the pangea-db Service and Secret.
    postgres_host: str = "pangea-db"
    postgres_port: int = 5432
    postgres_db: str = "pangea"
    postgres_user: str = "pangea_app"
    postgres_password: str = "pangea_app_pw"

    # Connection pool sizing.
    db_pool_min: int = 1
    db_pool_max: int = 10

    # --- HTTP -------------------------------------------------------------
    # Comma-separated list of allowed CORS origins for the dashboard UI.
    # "*" is convenient for the demo; lock this down for real deployments.
    cors_origins: str = "*"

    # --- Solar prediction -------------------------------------------------
    # KServe v2 inference endpoint for the solar-output ONNX model (in-cluster
    # service; no route needed). Reads outputs[0].data as predicted kW/unit.
    solar_model_url: str = (
        "http://solar-output-predictor.pangea-energy-and-power.svc.cluster.local"
        "/v2/models/solar-output/infer"
    )
    # Open-Meteo forecast API (no API key, permissive licence). If unreachable
    # (e.g. egress-restricted cluster) the backend falls back to a clear-sky sim.
    open_meteo_url: str = "https://api.open-meteo.com/v1/forecast"
    # Peak output (kW) of the single plant the model was trained on; used to
    # scale the model's per-unit prediction up to each site's capacity_kw.
    solar_model_peak_kw: float = 233.0
    # How long to cache each site's forecast before recomputing (seconds).
    solar_cache_ttl_seconds: int = 1800
    # Timeout for outbound HTTP (weather + inference), seconds.
    solar_http_timeout: float = 8.0

    # --- Voice (speech-to-text) ------------------------------------------
    # In-cluster vLLM Whisper predictor (OpenAI-compatible audio API).
    whisper_url: str = (
        "http://whisper-tiny-predictor.pangea-energy-and-power.svc.cluster.local"
        "/v1/audio/transcriptions"
    )
    whisper_model: str = "whisper-tiny"
    # Pinned language ("" to auto-detect; tiny mis-detects short clips).
    whisper_language: str = "en"
    whisper_http_timeout: float = 60.0
    whisper_max_upload_bytes: int = 25 * 1024 * 1024

    # --- Agent (conversational LLM) --------------------------------------
    # OpenAI-compatible chat endpoint for the tool-calling agent: Granite 4.0
    # 350M (dense) served on the vLLM CPU runtime via KServe. The model name
    # must match what vLLM reports at /v1/models (here "granite4").
    agent_llm_url: str = (
        "http://granite4-predictor.pangea-energy-and-power.svc.cluster.local"
        "/v1/chat/completions"
    )
    agent_llm_model: str = "granite4"
    agent_http_timeout: float = 120.0
    agent_max_tokens: int = 512
    agent_temperature: float = 0.35
    # Anti-repetition penalties: keep the small model from looping on history.
    agent_repetition_penalty: float = 1.2
    agent_frequency_penalty: float = 0.4
    agent_presence_penalty: float = 0.4
    agent_system_prompt: str = (
        "You are Pangea, the operations assistant for the Pangea Energy and "
        "Power fleet (wind, solar and wave sites). Answer the supervisor "
        "concisely and factually using the dashboard state given as context. "
        "Prefer specific numbers and site names. If the context does not "
        "contain the answer, say so plainly. Keep replies to a sentence or two."
    )

    # --- Wave prediction --------------------------------------------------
    # Open-Meteo Marine forecast API (no API key, permissive licence): live
    # significant wave height + period drive the wave-farm output estimate.
    # Unreachable -> a deterministic calm-sea fallback keeps the demo alive.
    open_meteo_marine_url: str = "https://marine-api.open-meteo.com/v1/marine"
    # How long to cache each wave site's forecast before recomputing (seconds).
    wave_cache_ttl_seconds: int = 1800

    # --- Wind cut-out -----------------------------------------------------
    # Above this hub-height wind speed (m/s) turbines shut down for storm
    # protection; the site is reported as shut down and a high-wind alert fires.
    wind_cutout_ms: float = 22.0
    wind_cache_ttl_seconds: int = 600
    wind_http_timeout: float = 8.0

    @property
    def dsn(self) -> str:
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


settings = Settings()
