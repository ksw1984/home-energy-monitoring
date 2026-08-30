FROM python:3.13-slim AS builder

ENV PIP_NO_CACHE_DIR=1

WORKDIR /build

# Install Poetry
RUN pip install --no-cache-dir poetry

# Copy dependency definitions first for Docker layer caching
COPY pyproject.toml poetry.lock ./

# Install production dependencies
RUN poetry config virtualenvs.create false \
    && poetry install --only main --no-interaction --no-ansi --no-root

# Copy application source
COPY src ./src

# Install the application
RUN poetry install --only main --no-interaction --no-ansi


FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Copy installed Python packages and executables
COPY --from=builder /usr/local/lib/python3.13/site-packages \
    /usr/local/lib/python3.13/site-packages

COPY --from=builder /usr/local/bin \
    /usr/local/bin

# Copy application source
COPY --from=builder /build/src ./src

# Start Home Energy Monitoring
CMD ["home-energy-monitoring"]
