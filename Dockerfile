# Multi-stage production Dockerfile for Text-Book-RAG
FROM python:3.11-slim as base

# Prevent Python from writing .pyc files and enable unbuffered logging
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000 \
    DAILY_REQUEST_LIMIT=50

WORKDIR /app

# Install system dependencies (curl for container healthcheck)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source code and pre-indexed database
COPY ingestion/ ./ingestion/
COPY retrieval/ ./retrieval/
COPY context/ ./context/
COPY agent/ ./agent/
COPY ui/ ./ui/
COPY run_pipeline.py .
COPY textbook_rag.db .
COPY README.md .

# Create non-root user for security
RUN useradd -m -u 1000 appuser && \
    chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# Health check to ensure web server is responding
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/ || exit 1

# Start FastAPI application
CMD ["uvicorn", "ui.app:app", "--host", "0.0.0.0", "--port", "8000"]
