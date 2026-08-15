FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt
ENV SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt

WORKDIR /app

RUN addgroup --system bankrotai && adduser --system --ingroup bankrotai --home /app bankrotai

COPY pyproject.toml README.md requirements.lock ./
COPY src ./src
COPY tests ./tests
COPY alembic ./alembic
COPY alembic.ini ./
COPY certs/russian_trusted_root_ca.crt certs/russian_trusted_sub_ca.crt certs/russian_trusted_sub_ca_2024.crt /usr/local/share/ca-certificates/

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && update-ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.lock \
    && pip install --no-cache-dir --no-deps .

RUN chown -R bankrotai:bankrotai /app
USER bankrotai

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/live', timeout=5)"

CMD ["python", "-m", "bankrotai.cli", "run-api", "--host", "0.0.0.0", "--port", "8000"]
