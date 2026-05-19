FROM python:3.11-slim

# Non-root user for security
RUN groupadd -r appuser && useradd -r -g appuser -d /app -s /sbin/nologin appuser

WORKDIR /app

# Install dependencies first (layer caching — only re-runs if requirements change)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY app/ app/

# Switch to non-root user
USER appuser

# Cloud Run sets PORT env var; default to 8080
ENV PORT=8080

EXPOSE ${PORT}

CMD exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT}
