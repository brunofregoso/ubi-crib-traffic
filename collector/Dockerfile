FROM python:3.11-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends cron tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY db/ ./db/
COPY collector/ ./collector/

RUN chmod +x /app/collector/entrypoint.sh

# .env is mounted or provided via `docker run --env-file`; nothing
# secret is baked into the image.
ENTRYPOINT ["/app/collector/entrypoint.sh"]
