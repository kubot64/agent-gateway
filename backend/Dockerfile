FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY backend/src ./src
COPY backend/pyproject.toml ./pyproject.toml
COPY README.md ./README.md
COPY docs ./docs

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir \
      fastapi>=0.110.0 \
      uvicorn>=0.27.0 \
      python-multipart>=0.0.9 \
      pynacl>=1.5.0 \
      "psycopg[binary]>=3.2.0" \
      redis>=5.0.0

EXPOSE 8000

ENV PYTHONPATH=/app/src

CMD ["python", "-m", "uvicorn", "gateway.main:app", "--host", "0.0.0.0", "--port", "8000"]
