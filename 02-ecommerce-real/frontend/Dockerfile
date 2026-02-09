# =============================================================================
# Dockerfile for ecommerce-frontend
# Final image target: < 60MB on node:18-alpine
# =============================================================================

FROM node:18-alpine

# Non-root user for security
RUN addgroup -S appgroup && adduser -S appuser -G appgroup

WORKDIR /app

# Install dependencies first (layer caching)
COPY package.json package-lock.json* ./
RUN if [ -f package-lock.json ]; then \
      npm ci --omit=dev --ignore-scripts; \
    else \
      npm install --omit=dev --no-audit --no-fund; \
    fi \
    && npm cache clean --force

# Copy application code
COPY server.js ./
COPY public/ ./public/

# Set ownership and switch to non-root
RUN chown -R appuser:appgroup /app
USER appuser

EXPOSE 3000

CMD ["node", "server.js"]
