#!/bin/sh
# Run a DCF from any POSIX shell: once through the CLI, once through the REST API.
#   sh examples/shell/valuation.sh
#   FIN_API_URL=http://127.0.0.1:8000 FIN_API_KEY=... sh examples/shell/valuation.sh
set -eu
CONFIG='{"name": "Acme (from sh)", "model_type": "dcf",
  "periods": {"start_year": 2025, "years": 5, "type": "annual"},
  "assumptions": {"drivers": {"revenue": {"value": 10000000},
                              "revenue_growth": {"value": [0.3, 0.25, 0.2, 0.15, 0.1]}}}}'

if [ -n "${FIN_API_URL:-}" ]; then
  # REST API: POST /dcf with {"config": ...}; the response is JSON.
  curl -fsS "$FIN_API_URL/dcf" -H "Content-Type: application/json" \
    -H "X-API-Key: ${FIN_API_KEY:-}" -d "{\"config\": $CONFIG}"
else
  # CLI: config on stdin, result JSON on stdout (pipe it to jq, a file, another program...).
  printf '%s' "$CONFIG" | "${FINANCIAL_MODEL:-financial-model}" run - -o -
fi
