# Dockerfile for Notifier Web Backend (FastAPI)
FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements
COPY web/requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy shared modules for database + notification delivery
COPY notifier/db.py ./notifier/db.py
COPY notifier/notifications.py ./notifier/notifications.py
COPY notifier/__init__.py ./notifier/__init__.py

# Copy web application code
COPY web/ ./web/
COPY .env.example .env.example

# Create directory for database (will be mounted as volume)
RUN mkdir -p /app/data

# Expose port
EXPOSE 8000

# Default command
CMD ["uvicorn", "web.main:app", "--host", "0.0.0.0", "--port", "8000"]