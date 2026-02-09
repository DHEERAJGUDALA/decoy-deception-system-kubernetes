FROM node:18-alpine

WORKDIR /app

COPY package.json package-lock.json* ./
RUN if [ -f package-lock.json ]; then \
      npm ci --omit=dev --ignore-scripts; \
    else \
      npm install --omit=dev --no-audit --no-fund; \
    fi \
    && npm cache clean --force

COPY server.js .
USER node

EXPOSE 8081
CMD ["node", "server.js"]
