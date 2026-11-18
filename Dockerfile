FROM python:3.12-slim AS builder
RUN python -m venv /venv
COPY requirements.txt .
RUN /venv/bin/pip install --no-cache-dir -r requirements.txt \
    # pip n'a servi qu'à construire le venv : il ne part pas dans l'image finale.
    && /venv/bin/python -m pip uninstall -y pip

# --- Socle commun runtime/dev ------------------------------------------------
FROM python:3.12-slim AS base
# L'image python:slim embarque son propre pip système : retiré aussi (cf.
# CLAUDE.md, ni pip ni compilateur ni cache dans l'image finale).
RUN python -m pip uninstall -y pip \
    && rm -rf /root/.cache
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
# Construite uniquement par docker-compose.dev (`target: dev`).
FROM base AS dev
USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends socat \
    && rm -rf /var/lib/apt/lists/*
COPY tools/ /app/tools/
USER bridge

# --- Image de production : minimale, sans socat ni outillage de test. --------
# DERNIER étage, donc cible par défaut : `docker build .` et le `build: .` du
# compose de production produisent cette image, jamais celle de dev.
FROM base AS runtime
