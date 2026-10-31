ARG PYTHON_BASE=python:3.12.15-alpine3.24@sha256:b658b05ce9c4eb97ec6fc19424818a1f31993659c2b505a532f470e83807815d
FROM ${PYTHON_BASE} AS builder
WORKDIR /build
COPY pyproject.toml ./
COPY deploy/python.lock ./deploy/python.lock
COPY core ./core
RUN --mount=type=secret,id=proxy_ca \
    set -eu; \
    PIP_CERT="${PIP_CERT:-/etc/ssl/certs/ca-certificates.crt}"; \
    if [ -f /run/secrets/proxy_ca ]; then PIP_CERT=/run/secrets/proxy_ca; fi; \
    export PIP_CERT; \
    pip install --only-binary=:all: --no-cache-dir pip==26.2.2 setuptools==83.0.0 wheel==0.46.2 && \
    pip wheel --no-cache-dir --no-deps --no-build-isolation -w /tmp/wheels . && \
    python -m venv /opt/venv && \
    /opt/venv/bin/pip install --only-binary=:all: --no-cache-dir -c deploy/python.lock /tmp/wheels/amora_chibi*.whl && \
    /opt/venv/bin/python -m pip uninstall -y pip

FROM ${PYTHON_BASE} AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    AMORA_MODE=cloud AMORA_HOME=/var/lib/amora AMORA_HOST=0.0.0.0 PORT=8765 \
    AMORA_WEB_ROOT=/opt/amora/web AMORA_AVATAR_ROOT=/opt/amora/avatar \
    PATH=/opt/venv/bin:/usr/local/bin:/usr/local/sbin:/usr/sbin:/usr/bin:/sbin:/bin
WORKDIR /opt/amora
RUN python -m pip uninstall -y pip && \
    if [ -f /etc/alpine-release ]; then \
      addgroup -g 10001 amora && adduser -D -u 10001 -G amora amora; \
    else useradd --uid 10001 --create-home amora; fi && \
    mkdir -p /var/lib/amora && chown amora:amora /var/lib/amora
COPY --from=builder /opt/venv /opt/venv
# Preserve editable catalogs at the paths resolved by the installed package.
COPY --chmod=0444 config/voice_profiles.json /opt/venv/lib/python3.12/config/voice_profiles.json
COPY --chmod=0444 config/people_profiles.json /opt/venv/lib/python3.12/config/people_profiles.json
RUN chmod 0755 /opt/venv/lib/python3.12/config
COPY web ./web
# Static modules must be readable by the non-root runtime even from a private checkout.
RUN chmod -R a+rX /opt/amora/web
COPY avatar ./avatar
USER 10001:10001
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8765')+'/ready',timeout=8)"
CMD ["amora", "serve"]