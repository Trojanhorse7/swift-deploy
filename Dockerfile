# This Dockerfile lives at the **repository root** .

# The API container still runs as a **non-root** user (UID/GID 1000)

FROM python:3.12-alpine AS base

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Set working directory
WORKDIR /app

# Add group and user
RUN addgroup -g 1000 app \
    && adduser -D -u 1000 -G app app \
    && apk add --no-cache curl \
    && mkdir -p /var/log/swiftdeploy \
    && chown -R app:app /var/log/swiftdeploy /app

# Copy requirements and install dependencies
COPY app/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the API code
COPY app ./app

# Run the API process as non-root
USER 1000:1000

# Expose the API port
EXPOSE 3000

# Start the API server
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${APP_PORT:-3000}"]
