# =============================================================================
# Multi-stage build for product-service
# Final image target: < 80MB on python:3.11-alpine
# =============================================================================

# ---------------------------------------------------------------------------
# Stage 1: install dependencies into a virtual-env
# ---------------------------------------------------------------------------
FROM python:3.11-alpine AS builder

RUN apk add --no-cache gcc musl-dev libpq-dev

WORKDIR /build

COPY requirements.txt .
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir -r requirements.txt

# ---------------------------------------------------------------------------
# Stage 2: production image — only runtime deps, non-root user
# ---------------------------------------------------------------------------
FROM python:3.11-alpine

# libpq is the only runtime dependency for psycopg2
RUN apk add --no-cache libpq

# Copy the pre-built virtualenv from the builder stage
COPY --from=builder /opt/venv /opt/venv

# Ensure virtualenv binaries are on PATH
ENV PATH="/opt/venv/bin:$PATH"

# Non-root user for security
RUN addgroup -S appgroup && adduser -S appuser -G appgroup

WORKDIR /app
COPY app.py .

RUN chown -R appuser:appgroup /app
USER appuser

EXPOSE 8081

# Gunicorn: 2 workers, bind all interfaces on 8081
CMD ["gunicorn", "--workers", "2", "--bind", "0.0.0.0:8081", "app:app"]
