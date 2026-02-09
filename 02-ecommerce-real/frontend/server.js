/**
 * Frontend Server — Express reverse proxy + static file server
 *
 * Serves the single-page e-commerce UI from /public and proxies API requests
 * to the backend microservices (product-service, cart-service) running behind
 * Kubernetes ClusterIP services.
 *
 * Every response is tagged with X-Service-Node: real-frontend so the
 * monitoring layer can distinguish real traffic from decoy traffic.
 * All requests are logged to stdout in JSON format.
 */

const express = require('express');
const { createProxyMiddleware } = require('http-proxy-middleware');
const path = require('path');

// ---------------------------------------------------------------------------
// Configuration (env-driven for Kubernetes)
// ---------------------------------------------------------------------------
const PORT = parseInt(process.env.PORT || '3000', 10);
const PRODUCT_SERVICE_URL =
  process.env.PRODUCT_SERVICE_URL ||
  'http://product-service.ecommerce-real.svc.cluster.local:8081';
const CART_SERVICE_URL =
  process.env.CART_SERVICE_URL ||
  'http://cart-service.ecommerce-real.svc.cluster.local:8082';

const app = express();

// ---------------------------------------------------------------------------
// JSON request logging to stdout
// ---------------------------------------------------------------------------
app.use((req, res, next) => {
  const start = Date.now();
  res.on('finish', () => {
    const logEntry = {
      timestamp: new Date().toISOString(),
      method: req.method,
      path: req.originalUrl,
      source_ip: req.ip || req.socket.remoteAddress,
      user_agent: req.headers['user-agent'] || '',
      response_code: res.statusCode,
      duration_ms: Date.now() - start,
    };
    process.stdout.write(JSON.stringify(logEntry) + '\n');
  });
  next();
});

// ---------------------------------------------------------------------------
// X-Service-Node header on every response
// ---------------------------------------------------------------------------
app.use((_req, res, next) => {
  res.setHeader('X-Service-Node', 'real-frontend');
  next();
});

// ---------------------------------------------------------------------------
// Health check
// ---------------------------------------------------------------------------
app.get('/health', (_req, res) => {
  res.json({ status: 'healthy', service: 'frontend' });
});

// ---------------------------------------------------------------------------
// API proxy: /api/products/* → product-service:8081
// ---------------------------------------------------------------------------
app.use(
  createProxyMiddleware({
    pathFilter: (pathname) => pathname.startsWith('/api/products'),
    target: PRODUCT_SERVICE_URL,
    changeOrigin: true,
    on: {
      proxyRes: (proxyRes) => {
        proxyRes.headers['x-service-node'] = 'real-frontend';
      },
      error: (err, _req, res) => {
        const logEntry = {
          timestamp: new Date().toISOString(),
          level: 'ERROR',
          message: `Proxy error (product-service): ${err.message}`,
        };
        process.stdout.write(JSON.stringify(logEntry) + '\n');
        if (!res.headersSent) {
          res.status(502).json({ error: 'Product service unavailable' });
        }
      },
    },
  })
);

// ---------------------------------------------------------------------------
// API proxy: /api/cart/* → cart-service:8082
// ---------------------------------------------------------------------------
app.use(
  createProxyMiddleware({
    pathFilter: (pathname) => pathname.startsWith('/api/cart'),
    target: CART_SERVICE_URL,
    changeOrigin: true,
    on: {
      proxyRes: (proxyRes) => {
        proxyRes.headers['x-service-node'] = 'real-frontend';
      },
      error: (err, _req, res) => {
        const logEntry = {
          timestamp: new Date().toISOString(),
          level: 'ERROR',
          message: `Proxy error (cart-service): ${err.message}`,
        };
        process.stdout.write(JSON.stringify(logEntry) + '\n');
        if (!res.headersSent) {
          res.status(502).json({ error: 'Cart service unavailable' });
        }
      },
    },
  })
);

// ---------------------------------------------------------------------------
// Static files (the single-page app lives in /public)
// ---------------------------------------------------------------------------
app.use(express.static(path.join(__dirname, 'public')));

// SPA fallback — serve index.html for any unmatched route
app.get('*', (_req, res) => {
  res.sendFile(path.join(__dirname, 'public', 'index.html'));
});

// ---------------------------------------------------------------------------
// Start server
// ---------------------------------------------------------------------------
app.listen(PORT, '0.0.0.0', () => {
  const startLog = {
    timestamp: new Date().toISOString(),
    level: 'INFO',
    message: `Frontend server listening on port ${PORT}`,
    product_service: PRODUCT_SERVICE_URL,
    cart_service: CART_SERVICE_URL,
  };
  process.stdout.write(JSON.stringify(startLog) + '\n');
});
