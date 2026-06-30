# Concierge Agent — container image for Cloud Run (Day-5 deployability concept).
#
# Stateless FastAPI service. The only runtime secret is the Gemini API key, which
# is injected as an env var by Cloud Run (NEVER baked into the image — see README).
# Slim base + layer-cached deps keep the image small and cold-starts quick.

FROM python:3.12-slim

# Don't write .pyc files; flush stdout so Cloud Run captures logs in real time.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install dependencies first (separate layer) so they're cached unless
# requirements.txt changes — rebuilds after a code edit stay fast.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application code. .dockerignore keeps secrets/venv/caches out.
COPY . .

# Cloud Run sends traffic to the port named by $PORT (defaults to 8080). Bind
# uvicorn to it; shell form so ${PORT} is expanded at runtime.
ENV PORT=8080
CMD exec uvicorn api.main:app --host 0.0.0.0 --port ${PORT}
