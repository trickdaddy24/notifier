# syntax=docker/dockerfile:1
# ---- Stage 1: compile the Tailwind CSS from the templates ----
# Keeps web/static/app.css always in sync with the templates at build time,
# so adding Tailwind classes never requires a manual rebuild/commit.
FROM node:20-slim AS css
WORKDIR /build
COPY tailwind.config.js ./
COPY web/static/src/input.css ./web/static/src/input.css
COPY web/templates/ ./web/templates/
RUN npx -y tailwindcss@3.4.17 -i ./web/static/src/input.css -o ./web/static/app.css --minify

# ---- Stage 2: the FastAPI app ----
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
COPY notifier/holidays.py ./notifier/holidays.py
COPY notifier/__init__.py ./notifier/__init__.py

# Copy web application code
COPY web/ ./web/

# Overlay the freshly-built CSS (overrides the committed copy so the image
# always reflects the current templates)
COPY --from=css /build/web/static/app.css ./web/static/app.css

COPY .env.example .env.example

# Create directory for database (will be mounted as volume)
RUN mkdir -p /app/data

# Expose port
EXPOSE 8000

# Default command
CMD ["uvicorn", "web.main:app", "--host", "0.0.0.0", "--port", "8000"]
