# Self-hosting

The toolkit runs entirely on your own machine or server: models compute locally, chart
dashboards embed plotly.js (no CDN), and the only outbound calls are remote data providers
you choose to use (e.g. `fetch --provider yfinance`). For OS service managers, containers,
Kubernetes, PaaS and alternative ASGI servers, see [Deployment](deployment.md).

## Runtime settings

Settings are layered, highest priority first:

1. Environment variables `FIN_<SECTION>_<KEY>` (`FIN_API_PORT=9000`, `FIN_API_CORS_ORIGINS=https://a.com,https://b.com`)
2. A settings YAML: `--settings FILE`, else `$FIN_CONFIG_PATH`, else `./financial.yaml` or `./config/financial.yaml`
3. Built-in defaults

See [`config/financial.example.yaml`](https://github.com/echelonxventures-Projects/financials/blob/main/config/financial.example.yaml)
for every key. Model assumptions are separate: they live in the model YAML you pass to
`run` / the API.

| Key | Default | Effect |
|---|---|---|
| `api.host` | `127.0.0.1` | Bind address (`0.0.0.0` in the Docker image) |
| `api.port` | `8000` | Port |
| `api.workers` | `1` | Uvicorn worker processes |
| `api.cors_origins` | `[]` | Allowed browser origins; empty sends no CORS headers |
| `api.api_key` | empty | When set, every route except `/health`, `/docs`, `/redoc`, `/openapi.json` needs `X-API-Key` |
| `api.max_request_mb` | `10` | Larger bodies are rejected with 413 |
| `data.default_provider` | `yfinance` | Provider for `fetch` when the source is not a local path (CLI) or none is given (API) |
| `data.rate_limit.*` | 30/min, 500/h, breaker 5 failures / 60 s | Guards remote data providers |
| `logging.level` | `INFO` | Log level |
| `presentation.excel.*` | see `financial_model/settings.yaml` | Workbook look: fonts, header fill, borders, number formats, %/x hints, column widths, sheet and column titles, frozen panes |
| `presentation.charts.*` | see `financial_model/settings.yaml` | Chart look: palette, positive/negative/total colours, themes, size, fonts, margins, legend, dashboard page colours |
| `validation.tickers` / `validation.ranges` | see `financial_model/settings.yaml` | Ticker patterns per exchange; `[min, max]` bounds of named financial parameters |

Every built-in value is in `financial_model/settings.yaml`; a settings file only needs the keys
it changes (mappings deep-merge). Quote hex colours (`"112233"`), or YAML may read them as numbers.

## Running

```bash
pip install -e ".[api]"         # or ".[all]" for charts + yfinance too
financial-model serve                       # 127.0.0.1:8000
financial-model serve --host 0.0.0.0 --port 9000 --settings financial.yaml
```

### Containers (Docker, Podman, ...)

```bash
docker build -t financial-model .
docker run -d -p 8000:8000 -e FIN_API_KEY=change-me financial-model
curl -H "X-API-Key: change-me" localhost:8000/models
```

The image runs as a non-root user, listens on `0.0.0.0:8000`, and has a `HEALTHCHECK`
against `/health`. Mount a settings file with
`-v $PWD/financial.yaml:/app/financial.yaml` (auto-loaded from the working directory).

## Security notes

- Leave `api_key` empty only on a trusted network or behind an authenticating proxy.
- The `/ma` endpoint accepts inline acquirer/target configs only; file paths are rejected
  so requests cannot read files from the server.
- Tickers are validated before any network call; fetch failures open a circuit breaker.
