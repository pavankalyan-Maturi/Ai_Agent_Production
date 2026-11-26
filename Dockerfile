# syntax=docker/dockerfile:1
FROM python:3.12-slim AS build
# Optional extra CA for builds behind a TLS-inspecting proxy:
#   docker build --secret id=extra_ca,src=/path/ca.crt .
# Never baked into a layer.
ARG CA_SETUP='if [ -f /run/secrets/extra_ca ]; then export PIP_CERT=/run/secrets/extra_ca SSL_CERT_FILE=/run/secrets/extra_ca; fi'
RUN --mount=type=secret,id=extra_ca sh -c "$CA_SETUP; pip install --no-cache-dir uv==0.11.32"
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=secret,id=extra_ca sh -c "$CA_SETUP; uv sync --frozen --no-dev --no-install-project"
COPY apps ./apps
RUN --mount=type=secret,id=extra_ca sh -c "$CA_SETUP; uv sync --frozen --no-dev"

FROM python:3.12-slim
# Pull Debian security fixes newer than the base image (Trivy gates HIGH/CRITICAL with a fix).
RUN apt-get update -o APT::Update::Error-Mode=any && apt-get upgrade -y --no-install-recommends && rm -rf /var/lib/apt/lists/*
RUN groupadd --system app && useradd --system --gid app --home /app app
WORKDIR /app
COPY --from=build --chown=app:app /app /app
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
USER app
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --retries=3 \
  CMD python -c "import urllib.request,sys; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2)" || exit 1
CMD ["uvicorn", "--factory", "apps.api.main:create_app", "--host", "0.0.0.0", "--port", "8000"]
