# Pangea Energy and Power — Database

PostgreSQL 15 + `pgvector` schema for the Pangea platform. Stores sites, turbines,
time-series sensor data, ML predictions, alerts, maintenance/work-orders, parts,
operatives, and the RAG knowledge base for the conversational AI.

## Licensing (why no TimescaleDB)

Everything here uses the **PostgreSQL License** (OSI-approved, permissive, freely
redistributable), which is what the Red Hat AI Quickstart contribution requires.

- **PostgreSQL 15** — PostgreSQL License ✅
- **pgvector** — PostgreSQL License ✅
- **TimescaleDB** — intentionally NOT used. Its Community/TSL features (continuous
  aggregates, compression, retention automation) are *source-available* but **not
  OSI open source**. At this scale (~90 turbines, demo/Quickstart) we don't need
  them: time-series is stored in plain Postgres tables and rolled up with regular
  materialized views. If large-scale partitioning is ever needed, use native
  PostgreSQL declarative partitioning (also permissive).

## Layout

```
database/
├── README.md
├── Containerfile          # UBI9 postgresql-15 + pgvector (for OpenShift)
├── init/                  # SQL run in order on first container start
│   ├── 01-extensions.sql
│   ├── 02-types.sql
│   ├── 03-tables.sql
│   ├── 04-indexes.sql
│   ├── 05-views.sql
│   └── 06-functions.sql
└── seed/                  # seed data (added in a later step)
```

The `init/` scripts are numbered so they apply in order (mirrors how the Postgres
container's `/docker-entrypoint-initdb.d` and our OpenShift init flow run them).

## Time-series without TimescaleDB

`sensor_readings`, `ml_predictions`, and `wind_data` are ordinary tables keyed on
`(time, id)` with descending time indexes. Dashboard rollups (hourly/daily) are
regular `MATERIALIZED VIEW`s refreshed on a schedule (see `05-views.sql` and the
`refresh_rollups()` function in `06-functions.sql`).
