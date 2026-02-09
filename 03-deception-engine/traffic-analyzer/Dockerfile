# =============================================================================
# Multi-stage build for traffic-analyzer
# Final image target: < 60MB on python:3.11-alpine
# =============================================================================
# No native C dependencies (unlike product/cart services which need psycopg2).
# Flask + redis-py are pure Python, so no gcc/musl-dev required.
# =============================================================================

# ---------------------------------------------------------------------------
# Stage 1: install dependencies into a virtual-env
# ---------------------------------------------------------------------------
FROM python:3.11-alpine AS builder

WORKDIR /build

COPY requirements.txt .
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir -r requirements.txt

# ---------------------------------------------------------------------------
# Stage 2: production image — only runtime, non-root user
# ---------------------------------------------------------------------------
FROM python:3.11-alpine

# Copy the pre-built virtualenv from the builder stage
COPY --from=builder /opt/venv /opt/venv

# Ensure virtualenv binaries are on PATH
ENV PATH="/opt/venv/bin:$PATH"

# Non-root user for security
RUN addgroup -S appgroup && adduser -S appuser -G appgroup

WORKDIR /app
COPY attack_patterns.py .
COPY analyzer.py .

RUN chown -R appuser:appgroup /app
USER appuser

EXPOSE 8085

# Gunicorn: 2 workers, bind all interfaces on 8085
# --preload loads the app once and forks — saves memory on our i3 box
CMD ["gunicorn", "--workers", "2", "--bind", "0.0.0.0:8085", "--preload", "analyzer:app"]
