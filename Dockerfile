FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN mkdir -p /app/output

# Versionsnummer (fortlaufende Commit-Anzahl) wird beim Build von GitHub Actions
# als Build-Arg übergeben und als Umgebungsvariable verfügbar gemacht.
ARG APP_VERSION=""
ENV APP_VERSION=${APP_VERSION}

EXPOSE 5050
CMD ["gunicorn", "--bind", "0.0.0.0:5050", "--workers", "2", "--timeout", "60", "app:app"]
