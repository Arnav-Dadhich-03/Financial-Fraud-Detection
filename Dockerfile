FROM python:3.11-slim

WORKDIR /app

# System deps for matplotlib/seaborn rendering (headless)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# fraud_model.pkl and stream_data.pkl are committed to the repo, but if
# they're ever missing (e.g. fresh clone without the pkl files), generate
# them at build time so the image is always runnable.
RUN [ -f fraud_model.pkl ] && [ -f stream_data.pkl ] || python fraud_detection_pipeline.py

EXPOSE 8000

# Respects $PORT if the platform sets one (Render, Railway, etc.),
# otherwise falls back to 8000 for local `docker run -p 8000:8000`.
CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT:-8000}"]
