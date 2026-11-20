FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SIMPLECLOUD_ENV_FILE=/nonexistent \
    DATABASE=/data/database.db \
    UPLOAD_FOLDER=/data/uploads \
    BACKUP_FOLDER=/data/backups \
    PROXY_COUNT=1

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
 && useradd --system --uid 10001 --home-dir /data --shell /usr/sbin/nologin simplecloud \
 && mkdir -p /data && chown simplecloud:simplecloud /data
COPY app.py ./
COPY simplecloud ./simplecloud
COPY templates ./templates
COPY static ./static

USER simplecloud
VOLUME ["/data"]
EXPOSE 8000
# Migrate (backs up first; no-op when current), then serve.
CMD ["sh", "-c", "flask --app app migrate && exec gunicorn --workers ${WORKERS:-4} --bind 0.0.0.0:8000 --timeout 600 app:app"]
