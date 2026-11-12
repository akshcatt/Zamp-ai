FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 DATA_DIR=/app/data
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
# Render (and most hosts) inject $PORT; 10000 is Render's default. exec so uvicorn gets signals directly.
EXPOSE 10000
CMD ["sh", "-c", "echo \"ClearAP: starting on port ${PORT:-10000}\" && exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-10000} --proxy-headers --forwarded-allow-ips='*' --log-level info"]
