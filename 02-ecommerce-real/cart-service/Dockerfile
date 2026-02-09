# =============================================================================
# Multi-stage build for cart-service
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

RUN apk add --no-cache libpq

COPY --from=builder /opt/venv /opt/venv

ENV PATH="/opt/venv/bin:$PATH"

RUN addgroup -S appgroup && adduser -S appuser -G appgroup

WORKDIR /app
COPY app.py .

RUN chown -R appuser:appgroup /app
USER appuser

EXPOSE 8082

CMD ["gunicorn", "--workers", "2", "--bind", "0.0.0.0:8082", "app:app"]
