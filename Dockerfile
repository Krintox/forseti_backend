# FORSETI backend — single image, deployable as-is to Render, Hugging Face
# Spaces (Docker SDK), Fly.io, or any container host.
#
# Build context is the REPO ROOT (not backend/), because app/paths.py resolves
# artifacts/ as two directories above backend/app/ — this image reproduces
# that layout exactly, so nothing in the app code has to change for a
# container. ARTIFACTS_DIR is also set explicitly below as a second guarantee.
#
# Listens on $PORT if the platform sets one (Render does), otherwise 7860
# (Hugging Face Spaces' default and expected app_port).

FROM python:3.11-slim

# xgboost/lightgbm link against OpenMP at runtime; libgomp1 is the only native
# package this stack needs (dilithium-py is pure Python, matplotlib runs
# headless via MPLBACKEND=Agg below).
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependencies first, so the layer caches across code-only changes.
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

# Application code and the already-trained model/evaluation artifacts. The
# model is 250KB and the whole artifacts/ directory is ~15MB, so shipping the
# trained model in the image is deliberate: it means the deployed API serves
# real scores immediately, with no training step in the build.
COPY backend/ backend/
COPY artifacts/ artifacts/

ENV ARTIFACTS_DIR=/app/artifacts \
    PYTHONUNBUFFERED=1 \
    MPLBACKEND=Agg \
    PORT=7860

EXPOSE 7860

WORKDIR /app/backend

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
