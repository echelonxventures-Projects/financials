# OCI image for the Financial Model API. Builds with docker, podman, buildah, nerdctl or kaniko.
# One of several deployment options - see docs/deployment.md (pip, systemd, launchd, Windows, PaaS, Kubernetes).
#   docker build -t financial-model .
#   docker run -p 8000:8000 -e FIN_API_KEY=change-me financial-model
# Settings come from FIN_* env vars or a mounted settings file (FIN_CONFIG_PATH);
# see docs/self-hosting.md.

# Tracks the newest Python 3 release by default; pin one for reproducible builds:
#   docker build --build-arg PYTHON_VERSION=3.12 -t financial-model .
ARG PYTHON_VERSION=3

# ---- builder ----
FROM python:${PYTHON_VERSION}-slim AS builder
WORKDIR /src
COPY pyproject.toml README.md LICENSE ./
COPY financial_model ./financial_model
RUN pip install --no-cache-dir --prefix=/install ".[all]"

# ---- runtime ----
FROM python:${PYTHON_VERSION}-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    FIN_API_HOST=0.0.0.0 \
    FIN_API_PORT=8000
RUN useradd --create-home --uid 10001 app
COPY --from=builder /install /usr/local
WORKDIR /app
COPY --chown=app:app config ./config
USER app

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)" || exit 1

CMD ["financial-model", "serve"]
