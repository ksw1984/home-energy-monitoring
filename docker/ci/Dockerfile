FROM python:3.13-slim

ENV POETRY_VERSION=2.4.1 \
    POETRY_NO_INTERACTION=1 \
    POETRY_VIRTUALENVS_CREATE=false

RUN pip install --no-cache-dir "poetry==${POETRY_VERSION}"

WORKDIR /workspace

COPY pyproject.toml poetry.lock ./

RUN poetry install --with dev --no-root
