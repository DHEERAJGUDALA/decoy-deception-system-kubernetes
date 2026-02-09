const express = require('express');
const path = require('path');
const { createProxyMiddleware } = require('http-proxy-middleware');

const PORT = parseInt(process.env.PORT || '8080', 10);
const EVENT_COLLECTOR_WS =
  process.env.EVENT_COLLECTOR_WS ||
  'ws://event-collector.monitoring.svc.cluster.local:8090';
const EVENT_COLLECTOR_API =
  process.env.EVENT_COLLECTOR_API ||
  'http://event-collector.monitoring.svc.cluster.local:8091';

const app = express();

app.get('/health', (_req, res) => {
  res.json({ status: 'ok', service: 'dashboard' });
});

app.get('/config', (_req, res) => {
  const host = _req.headers.host || `localhost:${PORT}`;
  const protocol = _req.protocol || 'http';
  const wsProtocol = protocol === 'https' ? 'wss' : 'ws';
  res.json({
    eventCollectorWs: `${wsProtocol}://${host}/ws`,
    eventCollectorApi: `${protocol}://${host}/proxy`,
  });
});

app.use(
  '/proxy',
  createProxyMiddleware({
    target: EVENT_COLLECTOR_API,
    changeOrigin: true,
    pathRewrite: { '^/proxy': '' },
  })
);

const wsProxy = createProxyMiddleware({
  target: EVENT_COLLECTOR_WS,
  changeOrigin: true,
  ws: true,
  pathRewrite: { '^/ws': '' },
});

app.use('/ws', wsProxy);

app.use('/vendor', express.static(path.join(__dirname, 'node_modules', 'd3', 'dist')));
app.use(express.static(path.join(__dirname, 'public')));

app.get('*', (_req, res) => {
  res.sendFile(path.join(__dirname, 'public', 'index.html'));
});

const server = app.listen(PORT, '0.0.0.0', () => {
  const log = {
    timestamp: new Date().toISOString(),
    level: 'INFO',
    message: `Dashboard server listening on port ${PORT}`,
    event_collector_ws: EVENT_COLLECTOR_WS,
    event_collector_api: EVENT_COLLECTOR_API,
  };
  process.stdout.write(JSON.stringify(log) + '\n');
});

server.on('upgrade', wsProxy.upgrade);
