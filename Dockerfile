FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 DATA_DIR=/app/data
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
# Run as an unprivileged user; only the data directory is writable.
RUN useradd --create-home --uid 10001 app && mkdir -p /app/data && chown -R app:app /app/data
USER app
EXPOSE 10000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import os,urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"10000\")}/healthz', timeout=4)" || exit 1
CMD ["sh", "-c", "echo \"NoCap: starting on port ${PORT:-10000}\" && exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-10000} --proxy-headers --forwarded-allow-ips='*' --log-level info"]
