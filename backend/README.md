# Pangea Energy and Power — backend API

Async FastAPI service that sits between the PostgreSQL + pgvector database and the
dashboard UI. **The UI talks only to this API**; this API is the only component
that holds database credentials (read from the `pangea-db-credentials` Secret).

## Endpoints (read-only, first pass)

| Method & path | Returns |
|---------------|---------|
| `GET /api/health` | Liveness + DB connectivity |
| `GET /api/fleet/status` | Turbine counts by status, live vs rated MW |
| `GET /api/sites` | Site list |
| `GET /api/turbines` | Turbine list (filters: `status`, `site_code`) |
| `GET /api/turbines/{code}` | Single turbine |
| `GET /api/turbines/{code}/readings?hours=24` | Recent sensor readings for charts |
| `GET /api/alerts?status=active` | Alert feed |

Interactive docs at `/docs` once running.

## Layout

```
backend/
├── app/
│   ├── main.py            # app, CORS, lifespan (pool up/down), router wiring
│   ├── config.py          # env-driven settings (DB creds from env/Secret)
│   ├── db.py              # asyncpg connection pool + fetch helpers
│   ├── models.py          # Pydantic v2 response shapes
│   └── routers/           # fleet.py, turbines.py, alerts.py
├── requirements.txt
├── Containerfile          # UBI9 python-311 base (OpenShift-friendly)
└── helm/pangea-api/       # Deployment + Service + Route
```

## Run locally (against a port-forwarded DB)

```bash
oc port-forward svc/pangea-db 5432:5432 &     # in the pangea namespace

cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

export POSTGRES_HOST=localhost POSTGRES_PORT=5432 \
       POSTGRES_DB=pangea POSTGRES_USER=pangea_app POSTGRES_PASSWORD=pangea_app_pw
uvicorn app.main:app --reload --port 8080

curl localhost:8080/api/fleet/status
```

## Deploy on OpenShift

The DB image is prebuilt, but this API is our own code, so it has to be built
once. Both paths below need **project-level permissions only**.

### 1. Build & push the image

**Option A — OpenShift internal build (no external registry):**

```bash
oc project pangea-energy-and-power

# Create the build config once, then build from the local backend/ dir.
oc new-build --name pangea-api --binary --strategy=docker
oc start-build pangea-api --from-dir=. --follow
```

This produces `image-registry.openshift-image-registry.svc:5000/pangea-energy-and-power/pangea-api:latest`
— which is the default `image.repository` in `values.yaml`.

**Option B — build locally and push to a registry (e.g. Quay):**

```bash
podman build -t quay.io/<you>/pangea-api:0.1.0 -f Containerfile .
podman push quay.io/<you>/pangea-api:0.1.0
```

### 2. Install the chart

```bash
# Option A (internal registry) — defaults already point at it:
helm install pangea-api helm/pangea-api

# Option B (external registry):
helm install pangea-api helm/pangea-api \
  --set image.repository=quay.io/<you>/pangea-api --set image.tag=0.1.0

oc rollout status deployment/pangea-api
```

### 3. Verify

```bash
oc exec deployment/pangea-api -- curl -s localhost:8080/api/health
curl "https://$(oc get route pangea-api -o jsonpath='{.spec.host}')/api/fleet/status"
```

## Configuration (`values.yaml`)

The chart is fully static (no `values.yaml`). Everything lives in
`templates/deployment.yaml` — Deployment, Service, and Route — with these fixed
settings: image
`image-registry.openshift-image-registry.svc:5000/pangea-energy-and-power/pangea-api:latest`,
DB host `pangea-db:5432`, credentials from Secret `pangea-db-credentials`
(`database-name/-user/-password`), `CORS_ORIGINS=*`, 1 replica, and an edge-TLS
`Route` so the dashboard can reach the API.

When the image moves to a public registry, edit the one `image:` line in
`templates/deployment.yaml`.

## Not yet wired

ML inference (OpenShift AI / KServe v2) and the conversational RAG endpoint over
pgvector come in a later pass — this skeleton is the read layer the dashboard
needs today.
