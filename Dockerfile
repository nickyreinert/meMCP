# Use a slim Python image for a smaller footprint and better security
FROM python:3.11-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

# Create and set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Create necessary directories
RUN mkdir -p db logs data .cache

# Copy application code
COPY . .

# Create a non-root user and switch to it for better security
RUN useradd -m mcpuser && \
    chown -R mcpuser:mcpuser /app
USER mcpuser

# Expose port 8000
EXPOSE 8000

# Command to run the application using Gunicorn with Uvicorn workers
# --proxy-headers is crucial because of the Nginx reverse proxy
CMD ["gunicorn", "app.main:app", "-w", "4", "-k", "uvicorn.workers.UvicornWorker", "--bind", "0.0.0.0:8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
