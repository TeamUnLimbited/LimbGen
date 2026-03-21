FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000 \
    JOBS_DIR=/app/instance/jobs \
    OPENSCAD_BIN=openscad \
    OPENSCAD_USE_XVFB=1 \
    MAX_RENDER_WORKERS=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends openscad xvfb xauth fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /app/instance/jobs

EXPOSE 8000

CMD ["gunicorn", "--workers", "1", "--threads", "8", "--timeout", "1800", "--bind", "0.0.0.0:8000", "app:app"]
