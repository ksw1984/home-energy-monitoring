FROM python:3.13-slim AS builder

ENV PIP_NO_CACHE_DIR=1
WORKDIR /build
RUN pip install --no-cache-dir poetry
COPY pyproject.toml poetry.lock ./
RUN poetry config virtualenvs.create false \
    && poetry install --only main --no-interaction --no-ansi --no-root

FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY --from=builder /usr/local/lib/python3.13/site-packages /usr/local/lib/python3.13/site-packages
COPY src/__init__.py ./src/__init__.py
COPY src/hems ./src/hems
COPY src/shared ./src/shared
COPY src/config ./src/config
COPY src/databases ./src/databases
COPY src/logger ./src/logger
CMD ["python", "-m", "src.hems.app"]
