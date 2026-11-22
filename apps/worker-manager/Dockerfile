FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY apps/api/pyproject.toml ./pyproject.toml
COPY apps/api/migrations ./migrations
COPY apps/api/app ./app
RUN pip install .
CMD ["python", "-m", "app.runtime.worker_manager"]
