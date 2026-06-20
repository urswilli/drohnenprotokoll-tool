FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

# Non-Root User: verhindert Privilege-Escalation aus dem Container
# Hinweis: Portainer-Stack ./data auf der VM muss uid 1001 gehören:
#   sudo chown -R 1001:1001 /var/lib/portainer/compose/<stack-id>/data
RUN addgroup --system --gid 1001 appgroup \
    && adduser --system --uid 1001 --ingroup appgroup --no-create-home appuser \
    && mkdir -p /app/output \
    && chown -R appuser:appgroup /app

# Versionsnummer (fortlaufende Commit-Anzahl) wird beim Build von GitHub Actions
# als Build-Arg übergeben und als Umgebungsvariable verfügbar gemacht.
ARG APP_VERSION=""
ENV APP_VERSION=${APP_VERSION}

USER appuser
EXPOSE 5050
CMD ["gunicorn", "--bind", "0.0.0.0:5050", "--workers", "2", "--timeout", "60", "app:app"]
