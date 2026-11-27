# LiteLLM proxy, pinned (ADR 011). Built from PyPI on our own slim base instead of the upstream
# image so the version, user and attack surface are ours: non-root, no DB, admin routes blocked
# by gateway/litellm.yaml, admin UI disabled, docs off.
FROM python:3.12-slim
ARG LITELLM_VERSION=1.110.0
RUN pip install --no-cache-dir "litellm[proxy]==${LITELLM_VERSION}" \
 && useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin litellm
COPY gateway/litellm.yaml /etc/litellm/config.yaml
ENV DISABLE_ADMIN_UI=True NO_DOCS=True NO_REDOC=True LITELLM_LOG=WARNING \
    LITELLM_TELEMETRY=False HOME=/tmp
USER 10001
EXPOSE 4000
CMD ["litellm", "--config", "/etc/litellm/config.yaml", "--host", "0.0.0.0", "--port", "4000", "--num_workers", "2"]
