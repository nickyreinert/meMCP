# Admin Backend Dockerfile
# Runs the MCP admin backend on port 8081 in a separate container

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies (same requirements as main app)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Create necessary directories
RUN mkdir -p db logs data .cache

# Copy application code
COPY . .

# Create non-root user
RUN useradd -m adminuser && \
    chown -R adminuser:adminuser /app
USER adminuser

# Expose admin port
EXPOSE 8081

# Run admin backend with gunicorn
CMD ["gunicorn", "admin.main:app", "-w", "2", "-k", "uvicorn.workers.UvicornWorker", "--bind", "0.0.0.0:8081"]
