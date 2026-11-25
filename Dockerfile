FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml ./
COPY src ./src

RUN pip install --no-cache-dir . \
    && adduser --disabled-password --gecos "" app \
    && mkdir -p /app/data \
    && chown -R app:app /app/data

USER app
VOLUME ["/app/data"]

CMD ["python", "-m", "app"]
