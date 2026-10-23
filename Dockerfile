ARG PYTHON_BASE=python:3.12-slim@sha256:a0f57064cbdcd6285fa45f51237ab0b35ea8ebaed06a1f5973e89e19dfb86704
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
    pip install --no-cache-dir pip==26.2.2 setuptools==83.0.0 wheel==0.46.2 && \
    pip wheel --no-cache-dir --no-deps --no-build-isolation -w /tmp/wheels . && \
    python -m venv /opt/venv && \
    /opt/venv/bin/pip install --no-cache-dir -c deploy/python.lock /tmp/wheels/amora_chibi*.whl && \
    /opt/venv/bin/python -m pip uninstall -y pip

FROM ${PYTHON_BASE} AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    AMORA_MODE=cloud AMORA_HOME=/var/lib/amora AMORA_HOST=0.0.0.0 PORT=8765 \
    AMORA_WEB_ROOT=/opt/amora/web AMORA_AVATAR_ROOT=/opt/amora/avatar \
    PATH=/opt/venv/bin:/usr/local/bin:/usr/local/sbin:/usr/sbin:/usr/bin:/sbin:/bin
WORKDIR /opt/amora
RUN python -m pip uninstall -y pip && \
    useradd --uid 10001 --create-home amora && mkdir -p /var/lib/amora && chown amora:amora /var/lib/amora
COPY --from=builder /opt/venv /opt/venv
COPY web ./web
COPY avatar ./avatar
USER 10001:10001
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8765')+'/ready',timeout=8)"
CMD ["amora", "serve"]
