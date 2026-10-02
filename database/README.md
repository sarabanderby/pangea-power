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
  OSI open source**. At this scale (~100 turbines, demo/Quickstart) we don't need
  them: time-series is stored in plain Postgres tables and rolled up with regular
  materialized views. If large-scale partitioning is ever needed, use native
  PostgreSQL declarative partitioning (also permissive).

## Layout

```
database/
├── README.md
└── helm/pangea-db/             # the chart — nothing to build
    ├── values.yaml             # image, seed toggle, storage, credentials
    ├── templates/              # StatefulSet, Service, Secret, ConfigMap
    └── files/
        ├── init/               # schema
        │   ├── 01-extensions.sql
        │   ├── 02-types.sql
        │   ├── 03-tables.sql
        │   ├── 04-indexes.sql
        │   ├── 05-views.sql
        │   └── 06-functions.sql
        └── seed/               # demo fleet, included when seed=true
            ├── 07-seed-sites.sql
            ├── 08-seed-turbines.sql
            ├── 09-seed-timeseries.sql
            └── 10-seed-operations.sql
```

There is no image to build: the chart runs the community `pgvector/pgvector:pg15`
image (Postgres 15 with pgvector, Debian-based) straight from Docker Hub.

Both directories are globbed into a ConfigMap and mounted at
`/docker-entrypoint-initdb.d`. The Postgres entrypoint runs every `*.sql` there
in filename order, but **only when the data directory is first initialised** —
so the numbering keeps the schema ahead of the seed, and re-installing over an
existing volume applies nothing. The seed files are included only when
`seed=true` (the default).

## Time-series without TimescaleDB

`sensor_readings`, `ml_predictions`, and `wind_data` are ordinary tables indexed
on `(id, time DESC)`. Dashboard rollups (hourly/daily) are regular
`MATERIALIZED VIEW`s — `sensor_readings_hourly`, `daily_prediction_summary` and
`wind_data_hourly` in `05-views.sql` — refreshed by `refresh_rollups()` in
`06-functions.sql`.

Nothing in the chart schedules that call yet. Run it hourly from a CronJob or
the API layer if you need the rollups kept current; the dashboard reads the
base tables directly, so it is unaffected either way.
