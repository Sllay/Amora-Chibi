# Heavy browser runtime is deliberately outside the tiny Alpine Amora Core.
# Match Chromium and Python Playwright versions; browsers are shipped in image.
FROM mcr.microsoft.com/playwright/python:v1.64.1-noble

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    AMORA_BROWSER_HOST=0.0.0.0 AMORA_BROWSER_PORT=8097
WORKDIR /opt/amora
RUN python -m pip install --no-cache-dir --only-binary=:all: \
    playwright==1.64.1 fastapi==0.121.3 uvicorn==0.38.0
COPY --chown=pwuser:pwuser workers/browser ./workers/browser
# Chromium sandbox needs a non-root process plus host user-namespace/seccomp
# support. Do not silently disable it if the platform lacks that support.
USER pwuser
EXPOSE 8097
CMD ["python", "-m", "uvicorn", "workers.browser.server:app", "--host", "0.0.0.0", "--port", "8097", "--no-access-log"]
