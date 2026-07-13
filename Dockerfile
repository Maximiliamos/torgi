FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
COPY tests ./tests
COPY alembic ./alembic
COPY alembic.ini ./

RUN pip install --upgrade pip && pip install .

EXPOSE 8000

CMD ["python", "-m", "bankrotai.cli", "run-api", "--host", "0.0.0.0", "--port", "8000"]
