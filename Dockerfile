FROM python:3.12-slim AS builder
RUN python -m venv /venv
COPY requirements.txt .
RUN /venv/bin/pip install --no-cache-dir -r requirements.txt

# --- Image de production : minimale, sans socat ni outillage de test. --------
FROM python:3.12-slim AS runtime
COPY --from=builder /venv /venv
COPY src/ /app/src/
WORKDIR /app
RUN useradd -r -u 1000 bridge && usermod -aG dialout bridge
USER bridge
ENV PATH=/venv/bin:$PATH \
    PYTHONPATH=/app/src \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
CMD ["python", "-m", "bridge"]

# --- Image de dev/essais : ajoute socat (port série virtuel) + simulateur. ---
# N'est jamais l'image par défaut : construite uniquement par docker-compose.dev.
FROM runtime AS dev
USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends socat \
    && rm -rf /var/lib/apt/lists/*
COPY tools/ /app/tools/
USER bridge
