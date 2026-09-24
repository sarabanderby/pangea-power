# Pangea Energy and Power — dashboard UI

First iteration of the frontend: the single-file dashboard (`dashboard-app.html`,
plain HTML/CSS/JS + Chart.js via CDN) served from a **UBI9 nginx** container that
also reverse-proxies `/api` to the `pangea-api` backend.

Because the API is proxied same-origin, the browser never calls the backend
directly — no CORS, and the backend needs no externally reachable Route.

## Live data

A small fetch layer wires these into the otherwise-static dashboard, with
graceful fallback to the demo values if the API is unavailable:

| Element | Endpoint |
|---------|----------|
| Global Fleet counts + Total Output | `GET /api/fleet/status` |
| Active Alerts list | `GET /api/alerts?status=active` |
| Predictive-maintenance vibration trend | `GET /api/turbines/NSO-04/readings?hours=48` |

Fleet + alerts refresh every 30s. Everything else in the dashboard is still
static demo content for this iteration.

## Layout

```
frontend/
├── dashboard-app.html         # the UI (served as index.html)
├── pange.png                  # welcome-screen / logo asset
├── nginx.default.d/
│   └── api-proxy.conf         # /api -> pangea-api:8080
├── Containerfile              # ubi9/nginx-124 base
└── helm/pangea-ui/            # Deployment + Service + Route (one static file)
```

## Deploy on OpenShift

Build the image (project-level perms; same pattern as pangea-api):

```bash
cd frontend
oc project pangea-energy-and-power

oc new-build --name pangea-ui --binary --strategy=docker
oc patch bc/pangea-ui --type=merge \
  -p '{"spec":{"strategy":{"dockerStrategy":{"dockerfilePath":"Containerfile"}}}}'
oc start-build pangea-ui --from-dir=. --follow
```

Then install the chart:

```bash
helm install pangea-ui helm/pangea-ui
oc rollout status deployment/pangea-ui
echo "https://$(oc get route pangea-ui -o jsonpath='{.spec.host}')"
```

## Notes

- The chart is fully static (no `values.yaml`). To move the image to a public
  registry later, edit the one `image:` line in
  `templates/deployment.yaml`.
- Local preview by opening `dashboard-app.html` in a browser still works; the
  `/api` calls just fail and the static demo values remain.
