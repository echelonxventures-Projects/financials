# Deployment

Nothing here is required: the toolkit is a Python package, and every option below runs the
same `financial-model serve` (or the plain ASGI app). Pick whatever your environment uses.

## Install profiles

| Install | Gets you |
|---|---|
| `pip install .` | Engine, all models, YAML configs, Excel/CSV/JSON export, the CLI, the `file` data provider |
| `pip install ".[api]"` | + REST API / `financial-model serve` (FastAPI, uvicorn) |
| `pip install ".[charts]"` | + `financial-model chart` / `POST /charts` (plotly) |
| `pip install ".[yfinance]"` | + the `yfinance` data provider |
| `pip install ".[all]"` | everything above |

`pipx`, `uv pip`, `poetry`, `pdm` and conda's pip all work the same way. A missing extra is
reported with the exact install command; it never breaks the rest of the toolkit.

## Where to run it

| Target | Recipe |
|---|---|
| Any OS, foreground | `financial-model serve --host 127.0.0.1 --port 8000` |
| Linux (systemd) | [`deploy/systemd/financial-model.service`](https://github.com/echelonxventures-Projects/financials/blob/main/deploy/systemd/financial-model.service) |
| macOS (launchd) | [`deploy/launchd/com.financial-model.api.plist`](https://github.com/echelonxventures-Projects/financials/blob/main/deploy/launchd/com.financial-model.api.plist) |
| Windows (Task Scheduler, no extra tools) | [`deploy/windows/install-service.ps1`](https://github.com/echelonxventures-Projects/financials/blob/main/deploy/windows/install-service.ps1) |
| Any OCI runtime (Docker, Podman, nerdctl, containerd, kaniko/buildah builds) | `Dockerfile` at the repo root |
| Compose (Docker / Podman / nerdctl) | [`deploy/compose.yaml`](https://github.com/echelonxventures-Projects/financials/blob/main/deploy/compose.yaml) |
| Kubernetes (any distribution) | [`deploy/kubernetes/financial-model.yaml`](https://github.com/echelonxventures-Projects/financials/blob/main/deploy/kubernetes/financial-model.yaml) |
| PaaS with buildpacks (Heroku, Render, Railway, Dokku, Scalingo...) | `Procfile` + `requirements.txt` at the repo root |

## Any ASGI server

`financial_model.api.main:app` is a standard ASGI application and `create_app()` is a
factory; both read the same settings (`FIN_*` env vars / settings file), so CORS, the API
key and limits apply whichever server runs them:

```bash
financial-model serve                                           # uvicorn (bundled with [api])
uvicorn --factory financial_model.api.main:create_app --port 8000
hypercorn financial_model.api.main:app --bind 0.0.0.0:8000
gunicorn -k uvicorn.workers.UvicornWorker financial_model.api.main:app
granian --interface asgi financial_model.api.main:app
```

Only uvicorn ships with the `[api]` extra and is exercised in CI; install the others
yourself. Put any reverse proxy (nginx, Caddy, Traefik, a cloud load balancer) in front for
TLS. The app serves no TLS itself.

## No library is required for the work itself

- **Data:** the `file` provider reads your own CSV / Excel / JSON statements offline; Yahoo
  Finance is optional, and other sources plug in as providers (see [Data sources](data.md)).
- **Charts:** dashboards embed plotly.js by default, so no CDN is needed (`--cdn` opts in).
- **Settings:** env vars, a YAML file, or both; there are no cloud or vendor services to configure.
