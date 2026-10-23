FROM python:3.12-slim@sha256:a0f57064cbdcd6285fa45f51237ab0b35ea8ebaed06a1f5973e89e19dfb86704
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    AMORA_MODE=cloud AMORA_HOME=/var/lib/amora AMORA_HOST=0.0.0.0 PORT=8765 \
    AMORA_WEB_ROOT=/opt/amora/web AMORA_AVATAR_ROOT=/opt/amora/avatar
WORKDIR /opt/amora
COPY pyproject.toml ./
COPY deploy/python.lock ./deploy/python.lock
COPY core ./core
COPY web ./web
COPY avatar ./avatar
RUN --mount=type=secret,id=proxy_ca \
    PIP_CERT="${PIP_CERT:-/etc/ssl/certs/ca-certificates.crt}"; \
    if [ -f /run/secrets/proxy_ca ]; then PIP_CERT=/run/secrets/proxy_ca; fi; \
    export PIP_CERT; \
    pip install --no-cache-dir -c deploy/python.lock setuptools==80.9.0 wheel==0.45.1 && \
    pip install --no-cache-dir --no-build-isolation -c deploy/python.lock . && \
    useradd --uid 10001 --create-home amora && mkdir -p /var/lib/amora && chown amora:amora /var/lib/amora
USER amora
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8765')+'/ready',timeout=8)"
CMD ["amora", "serve"]
