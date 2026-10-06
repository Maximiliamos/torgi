FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt
ENV SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

RUN addgroup --system bankrotai && adduser --system --ingroup bankrotai --home /app bankrotai

COPY pyproject.toml README.md requirements.lock ./
COPY certs/russian_trusted_root_ca.crt certs/russian_trusted_sub_ca.crt certs/russian_trusted_sub_ca_2024.crt /usr/local/share/ca-certificates/

RUN --mount=type=cache,target=/root/.cache/pip \
    sed -i 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list.d/debian.sources \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get -o Acquire::Retries=5 -o Acquire::ForceIPv4=true update \
    && apt-get -o Acquire::Retries=5 -o Acquire::ForceIPv4=true install -y --no-install-recommends ca-certificates \
    && update-ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --upgrade pip \
    && pip install -r requirements.lock \
    && python -m playwright install --with-deps chromium \
    && chmod -R a+rX /ms-playwright

COPY src ./src
COPY tests ./tests
COPY alembic ./alembic
COPY alembic.ini ./

RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --no-deps . \
    && chown -R bankrotai:bankrotai /app
USER bankrotai

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/live', timeout=5)"

CMD ["python", "-m", "bankrotai.cli", "run-api", "--host", "0.0.0.0", "--port", "8000"]
