# =============================================================================
# Multi-stage build for deception-controller
# Final image target: < 70MB on python:3.11-alpine
# =============================================================================
# The kubernetes Python client pulls in dependencies with optional C extensions
# (PyYAML with LibYAML, etc.), so the builder stage needs gcc/musl-dev.
# The runtime stage stays lean — only the pre-built virtualenv is copied over.
# =============================================================================

# ---------------------------------------------------------------------------
# Stage 1: install dependencies into a virtual-env
# ---------------------------------------------------------------------------
FROM python:3.11-alpine AS builder

# Build tools needed for kubernetes client's native deps (PyYAML C ext, etc.)
RUN apk add --no-cache gcc musl-dev libffi-dev

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
COPY decoy_templates.py .
COPY controller.py .

RUN chown -R appuser:appgroup /app
USER appuser

EXPOSE 8086

# Gunicorn: 1 worker — the controller runs background threads (Redis
# subscriber + TTL cleanup) that manage shared state. Multiple workers
# would duplicate these threads and cause double-processing of events.
# --preload loads the app once so threads start before the fork.
CMD ["gunicorn", "--workers", "1", "--bind", "0.0.0.0:8086", "--preload", "controller:app"]
