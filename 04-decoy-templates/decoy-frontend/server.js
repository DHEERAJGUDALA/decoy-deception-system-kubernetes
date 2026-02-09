/**
 * Decoy Frontend Server — Honeypot Express server
 *
 * Looks identical to the real e-commerce frontend but:
 *   - Serves fake product data (no real backend needed)
 *   - Logs EVERY request with full detail (headers, body, query params)
 *   - Detects path traversal, admin probes, and other recon patterns
 *   - Publishes all interactions to Redis pub/sub for real-time monitoring
 *   - Adds artificial delay to simulate real processing
 *   - Returns plausible fake responses for sensitive paths instead of 404
 */

const express = require('express');
const { createClient } = require('redis');
const path = require('path');

// ---------------------------------------------------------------------------
// Configuration
// ---------------------------------------------------------------------------
const PORT = parseInt(process.env.PORT || '3000', 10);
const DECOY_ID = process.env.DECOY_ID || 'decoy-frontend-001';
const REDIS_URL = process.env.REDIS_URL || 'redis://redis.monitoring.svc.cluster.local:6379';
const REDIS_CHANNEL = 'decoy_interaction';

const app = express();

// Parse JSON and URL-encoded bodies so we can log them
app.use(express.json({ limit: '1mb' }));
app.use(express.urlencoded({ extended: true, limit: '1mb' }));
// Also capture raw body for non-standard content types
app.use(express.raw({ type: '*/*', limit: '1mb' }));

// ---------------------------------------------------------------------------
// Redis client (non-blocking — if Redis is down, we still serve)
// ---------------------------------------------------------------------------
let redisClient = null;
let redisReady = false;

async function connectRedis() {
  try {
    redisClient = createClient({ url: REDIS_URL });
    redisClient.on('error', (err) => {
      redisReady = false;
      const log = { timestamp: new Date().toISOString(), level: 'WARN', message: `Redis error: ${err.message}` };
      process.stdout.write(JSON.stringify(log) + '\n');
    });
    redisClient.on('ready', () => { redisReady = true; });
    await redisClient.connect();
  } catch (err) {
    const log = { timestamp: new Date().toISOString(), level: 'WARN', message: `Redis connect failed: ${err.message}` };
    process.stdout.write(JSON.stringify(log) + '\n');
  }
}

async function publishEvent(event) {
  if (!redisReady || !redisClient) return;
  try {
    await redisClient.publish(REDIS_CHANNEL, JSON.stringify(event));
  } catch (_) { /* non-critical */ }
}

// ---------------------------------------------------------------------------
// Fake product data (different from real — slightly different names/prices)
// ---------------------------------------------------------------------------
const FAKE_PRODUCTS = [
  { id: 1, name: 'Wireless ANC Headphones Pro', description: 'Premium over-ear Bluetooth headphones with 40-hour battery life, hybrid ANC, and multipoint connection.', price: 84.99, image_url: '/images/products/headphones.jpg', category: 'electronics', stock_count: 52 },
  { id: 2, name: 'USB-C Hub 8-in-1 Pro', description: 'Aluminum adapter with dual HDMI 4K, 2x USB-A 3.0, USB-C data, SD/microSD readers, and 100W PD charging.', price: 39.99, image_url: '/images/products/usb-hub.jpg', category: 'electronics', stock_count: 98 },
  { id: 3, name: 'Mechanical Keyboard RGB TKL', description: 'Hot-swappable TKL keyboard with Gateron Brown switches, PBT keycaps, and per-key RGB with 16M colors.', price: 94.99, image_url: '/images/products/keyboard.jpg', category: 'electronics', stock_count: 27 },
  { id: 4, name: 'Portable Waterproof Speaker', description: 'IP68 rated Bluetooth speaker with 360-degree surround sound, 15-hour battery, and built-in powerbank.', price: 54.99, image_url: '/images/products/speaker.jpg', category: 'electronics', stock_count: 63 },
  { id: 5, name: 'Classic Fit Organic Cotton Tee', description: 'Sustainably sourced 100% organic cotton crew-neck. Pre-washed for softness. Available in 10 colors.', price: 27.99, image_url: '/images/products/tshirt.jpg', category: 'clothing', stock_count: 185 },
  { id: 6, name: 'Slim Fit Tech Chinos', description: 'Performance stretch chinos with moisture-wicking fabric, hidden zip pocket, and wrinkle-free finish.', price: 59.99, image_url: '/images/products/chinos.jpg', category: 'clothing', stock_count: 72 },
  { id: 7, name: 'Packable Rain Shell', description: 'Ultra-lightweight waterproof shell with taped seams, packable into chest pocket, and reflective trim.', price: 74.99, image_url: '/images/products/jacket.jpg', category: 'clothing', stock_count: 48 },
  { id: 8, name: 'Merino Wool Cuff Beanie', description: 'Fine-gauge merino wool beanie with fold-up cuff. Temperature-regulating and naturally antimicrobial.', price: 32.99, image_url: '/images/products/beanie.jpg', category: 'clothing', stock_count: 134 },
  { id: 9, name: 'Clean Code: Agile Software Handbook', description: 'Robert C. Martin\'s essential guide to writing clean, readable, and maintainable code. Paperback.', price: 38.99, image_url: '/images/products/clean-code.jpg', category: 'books', stock_count: 33 },
  { id: 10, name: 'Designing Data-Intensive Applications', description: 'Martin Kleppmann\'s deep dive into the architecture of reliable, scalable data systems. Hardcover edition.', price: 45.99, image_url: '/images/products/ddia.jpg', category: 'books', stock_count: 29 },
  { id: 11, name: 'The Pragmatic Programmer: 20th Ed.', description: 'Anniversary edition by Thomas & Hunt. Timeless practices for modern software developers.', price: 41.99, image_url: '/images/products/pragmatic.jpg', category: 'books', stock_count: 47 },
  { id: 12, name: 'Kubernetes in Action, 2nd Edition', description: 'Comprehensive guide to Kubernetes from pods to production. Updated for K8s 1.28+ with real-world patterns.', price: 52.99, image_url: '/images/products/k8s-book.jpg', category: 'books', stock_count: 21 },
];

// In-memory cart per session (fake — resets on restart, by design)
const fakeCarts = {};
let fakeCartIdCounter = 1000;
let fakeOrderIdCounter = 5000;

// ---------------------------------------------------------------------------
// Threat detection patterns
// ---------------------------------------------------------------------------
const HIGH_THREAT_PATHS = [
  /\/admin/i, /\/wp-admin/i, /\/wp-login/i, /\/wp-content/i,
  /\/\.env/i, /\/\.git/i, /\/\.htaccess/i, /\/\.htpasswd/i,
  /\/etc\/passwd/i, /\/etc\/shadow/i, /\/proc\/self/i,
  /\/phpmyadmin/i, /\/pma/i, /\/mysql/i, /\/myadmin/i,
  /\/server-status/i, /\/server-info/i,
  /\/actuator/i, /\/debug/i, /\/console/i,
  /\/config/i, /\/backup/i, /\/dump/i,
  /\/shell/i, /\/cmd/i, /\/exec/i,
  /\/api\/v1\/token/i, /\/graphql/i,
];

const PATH_TRAVERSAL_RE = /\.\.\//;
const SQLI_RE = /('|"|;|--|\bUNION\b|\bSELECT\b|\bDROP\b|\bINSERT\b|\bDELETE\b|\bUPDATE\b|\bOR\s+1\s*=\s*1|\bAND\s+1\s*=\s*1)/i;

function classifyThreat(req) {
  const fullPath = req.originalUrl || req.path;

  if (PATH_TRAVERSAL_RE.test(fullPath)) return 'path_traversal';
  for (const re of HIGH_THREAT_PATHS) {
    if (re.test(fullPath)) return 'high_threat';
  }

  // Check query params and body for SQLi
  const queryStr = JSON.stringify(req.query || {});
  const bodyStr = JSON.stringify(req.body || {});
  if (SQLI_RE.test(fullPath) || SQLI_RE.test(queryStr) || SQLI_RE.test(bodyStr)) {
    return 'sqli_attempt';
  }

  return 'normal';
}

// ---------------------------------------------------------------------------
// Fake responses for sensitive paths (look plausible, not 404)
// ---------------------------------------------------------------------------
const FAKE_SENSITIVE_RESPONSES = {
  admin: { html: '<html><head><title>Login - Admin Panel</title></head><body><div style="max-width:400px;margin:100px auto;font-family:sans-serif"><h2>Admin Login</h2><form method="POST" action="/admin/login"><label>Username</label><br><input type="text" name="username" style="width:100%;padding:8px;margin:8px 0"><br><label>Password</label><br><input type="password" name="password" style="width:100%;padding:8px;margin:8px 0"><br><button type="submit" style="padding:10px 20px;background:#0071e3;color:#fff;border:none;cursor:pointer;margin-top:8px">Sign In</button></form><p style="color:#999;font-size:12px">TechMart Admin Portal v2.1.4</p></div></body></html>' },
  env: { text: 'APP_ENV=production\nDB_HOST=db-primary.internal\nDB_PORT=5432\nDB_NAME=ecommerce_prod\nDB_USER=app_service\nDB_PASSWORD=xK9#mP2$vL7nQ4\nREDIS_URL=redis://cache.internal:6379\nSECRET_KEY=a1b2c3d4e5f6789012345678\nSTRIPE_KEY=sk_live_fake_4242424242424242\nAWS_ACCESS_KEY=AKIAIOSFODNN7EXAMPLE\nAWS_SECRET_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY\n' },
  git: { text: 'ref: refs/heads/main\n' },
  passwd: { text: 'root:x:0:0:root:/root:/bin/bash\ndaemon:x:1:1:daemon:/usr/sbin:/usr/sbin/nologin\nwww-data:x:33:33:www-data:/var/www:/usr/sbin/nologin\npostgres:x:999:999:PostgreSQL Server:/var/lib/postgresql:/bin/bash\nappuser:x:1000:1000:App User:/home/appuser:/bin/bash\n' },
  wp_login: { html: '<!DOCTYPE html><html><head><title>Log In &lsaquo; TechMart &#8212; WordPress</title></head><body class="login"><div id="login"><h1><a href="/">TechMart</a></h1><form name="loginform" id="loginform" action="/wp-login.php" method="post"><p><label for="user_login">Username or Email Address</label><input type="text" name="log" id="user_login" size="20"></p><p><label for="user_pass">Password</label><input type="password" name="pwd" id="user_pass" size="25"></p><p class="submit"><input type="submit" name="wp-submit" id="wp-submit" value="Log In"></p></form></div></body></html>' },
};

function getFakeSensitiveResponse(reqPath) {
  const p = reqPath.toLowerCase();
  if (p.includes('/admin')) return { type: 'html', body: FAKE_SENSITIVE_RESPONSES.admin.html };
  if (p.includes('/wp-admin') || p.includes('/wp-login')) return { type: 'html', body: FAKE_SENSITIVE_RESPONSES.wp_login.html };
  if (p.includes('.env')) return { type: 'text', body: FAKE_SENSITIVE_RESPONSES.env.text };
  if (p.includes('.git')) return { type: 'text', body: FAKE_SENSITIVE_RESPONSES.git.text };
  if (p.includes('/etc/passwd') || p.includes('/etc/shadow')) return { type: 'text', body: FAKE_SENSITIVE_RESPONSES.passwd.text };
  if (p.includes('/phpmyadmin') || p.includes('/pma') || p.includes('/myadmin')) {
    return { type: 'html', body: '<html><head><title>phpMyAdmin</title></head><body><h1>phpMyAdmin</h1><form method="post" action="/phpmyadmin/index.php"><label>Username:</label><input name="pma_username"><br><label>Password:</label><input type="password" name="pma_password"><br><button type="submit">Go</button></form></body></html>' };
  }
  return null;
}

// ---------------------------------------------------------------------------
// Artificial delay (100-500ms)
// ---------------------------------------------------------------------------
function randomDelay() {
  return new Promise((resolve) => {
    const ms = 100 + Math.floor(Math.random() * 400);
    setTimeout(resolve, ms);
  });
}

// ---------------------------------------------------------------------------
// Full request logger middleware (logs EVERYTHING)
// ---------------------------------------------------------------------------
app.use(async (req, res, next) => {
  const start = Date.now();
  const threat = classifyThreat(req);

  // Capture the session_id from cookies, body, or query
  const sessionId =
    (req.body && req.body.session_id) ||
    req.query.session_id ||
    req.headers['x-session-id'] ||
    '';

  res.on('finish', () => {
    const logEntry = {
      timestamp: new Date().toISOString(),
      type: 'decoy_interaction',
      threat_level: threat,
      decoy_id: DECOY_ID,
      source_ip: req.ip || req.socket.remoteAddress,
      method: req.method,
      path: req.originalUrl,
      headers: req.headers,
      query_params: req.query,
      body: typeof req.body === 'object' ? req.body : String(req.body || ''),
      user_agent: req.headers['user-agent'] || '',
      session_id: sessionId,
      response_code: res.statusCode,
      duration_ms: Date.now() - start,
    };
    process.stdout.write(JSON.stringify(logEntry) + '\n');

    // Publish to Redis
    publishEvent(logEntry);
  });

  next();
});

// ---------------------------------------------------------------------------
// X-Service-Node header
// ---------------------------------------------------------------------------
app.use((_req, res, next) => {
  res.setHeader('X-Service-Node', `decoy-frontend-${DECOY_ID}`);
  next();
});

// ---------------------------------------------------------------------------
// Health check (no delay, no threat logging overhead)
// ---------------------------------------------------------------------------
app.get('/health', (_req, res) => {
  res.json({ status: 'healthy', service: 'frontend' });
});

// ---------------------------------------------------------------------------
// High-threat path handler — return fake plausible responses
// ---------------------------------------------------------------------------
app.use(async (req, res, next) => {
  const threat = classifyThreat(req);
  if (threat === 'normal') return next();

  await randomDelay();

  const fakeResp = getFakeSensitiveResponse(req.originalUrl);
  if (fakeResp) {
    if (fakeResp.type === 'html') {
      res.type('html').send(fakeResp.body);
    } else {
      res.type('text').send(fakeResp.body);
    }
  } else {
    // Generic "forbidden" that still looks like a real server
    res.status(403).type('html').send(
      '<html><head><title>403 Forbidden</title></head><body><h1>Forbidden</h1><p>You don\'t have permission to access this resource.</p><hr><address>Apache/2.4.52 (Ubuntu) Server</address></body></html>'
    );
  }
});

// ---------------------------------------------------------------------------
// API: GET /api/products
// ---------------------------------------------------------------------------
app.get('/api/products', async (_req, res) => {
  await randomDelay();
  res.json(FAKE_PRODUCTS);
});

// ---------------------------------------------------------------------------
// API: GET /api/products/category/:category
// ---------------------------------------------------------------------------
app.get('/api/products/category/:category', async (req, res) => {
  await randomDelay();
  const filtered = FAKE_PRODUCTS.filter(
    (p) => p.category === req.params.category
  );
  res.json(filtered);
});

// ---------------------------------------------------------------------------
// API: GET /api/products/:id
// ---------------------------------------------------------------------------
app.get('/api/products/:id', async (req, res) => {
  await randomDelay();
  const id = parseInt(req.params.id, 10);
  const product = FAKE_PRODUCTS.find((p) => p.id === id);
  if (!product) {
    return res.status(404).json({ error: 'Product not found' });
  }
  res.json(product);
});

// ---------------------------------------------------------------------------
// API: GET /api/cart/:session_id
// ---------------------------------------------------------------------------
app.get('/api/cart/:session_id', async (req, res) => {
  await randomDelay();
  const sid = req.params.session_id;
  const cart = fakeCarts[sid] || [];
  res.json({ session_id: sid, items: cart });
});

// ---------------------------------------------------------------------------
// API: POST /api/cart/add
// ---------------------------------------------------------------------------
app.post('/api/cart/add', async (req, res) => {
  await randomDelay();
  const { session_id, product_id, quantity } = req.body || {};

  if (!session_id) return res.status(400).json({ error: 'session_id required' });

  const pid = parseInt(product_id, 10);
  const qty = parseInt(quantity, 10) || 1;
  const product = FAKE_PRODUCTS.find((p) => p.id === pid);
  if (!product) return res.status(404).json({ error: 'Product not found' });

  if (!fakeCarts[session_id]) fakeCarts[session_id] = [];

  const existing = fakeCarts[session_id].find((i) => i.product_id === pid);
  if (existing) {
    existing.quantity = Math.min(existing.quantity + qty, 99);
  } else {
    fakeCartIdCounter++;
    fakeCarts[session_id].push({
      cart_item_id: fakeCartIdCounter,
      quantity: qty,
      added_at: new Date().toISOString(),
      product_id: product.id,
      name: product.name,
      description: product.description,
      price: product.price,
      image_url: product.image_url,
      category: product.category,
    });
  }

  res.status(201).json({ session_id, items: fakeCarts[session_id] });
});

// ---------------------------------------------------------------------------
// API: DELETE /api/cart/:session_id/:item_id
// ---------------------------------------------------------------------------
app.delete('/api/cart/:session_id/:item_id', async (req, res) => {
  await randomDelay();
  const sid = req.params.session_id;
  const iid = parseInt(req.params.item_id, 10);

  if (fakeCarts[sid]) {
    fakeCarts[sid] = fakeCarts[sid].filter((i) => i.cart_item_id !== iid);
  }

  res.json({ message: 'Item removed', deleted_item_id: iid });
});

// ---------------------------------------------------------------------------
// API: POST /api/cart/:session_id/checkout
// ---------------------------------------------------------------------------
app.post('/api/cart/:session_id/checkout', async (req, res) => {
  await randomDelay();
  const sid = req.params.session_id;
  const cart = fakeCarts[sid] || [];

  if (cart.length === 0) return res.status(400).json({ error: 'Cart is empty' });

  const total = cart.reduce((s, i) => s + i.price * i.quantity, 0);
  fakeOrderIdCounter++;

  // Clear fake cart
  fakeCarts[sid] = [];

  res.status(201).json({
    order_id: fakeOrderIdCounter,
    session_id: sid,
    total_price: Math.round(total * 100) / 100,
    status: 'confirmed',
    created_at: new Date().toISOString(),
    items_count: cart.length,
  });
});

// ---------------------------------------------------------------------------
// Catch-all: POST to any path (captures form submissions)
// ---------------------------------------------------------------------------
app.post('*', async (req, res) => {
  await randomDelay();
  // Return a generic success — looks like a real form handler
  res.json({ status: 'success', message: 'Request processed' });
});

// ---------------------------------------------------------------------------
// Static files (decoy storefront)
// ---------------------------------------------------------------------------
app.use(express.static(path.join(__dirname, 'public')));

// SPA fallback
app.get('*', async (req, res) => {
  await randomDelay();
  res.sendFile(path.join(__dirname, 'public', 'index.html'));
});

// ---------------------------------------------------------------------------
// Start server
// ---------------------------------------------------------------------------
app.listen(PORT, '0.0.0.0', () => {
  const startLog = {
    timestamp: new Date().toISOString(),
    level: 'INFO',
    message: `Decoy frontend listening on port ${PORT}`,
    decoy_id: DECOY_ID,
    redis_url: REDIS_URL,
  };
  process.stdout.write(JSON.stringify(startLog) + '\n');
});

// Do not block startup on Redis connection; decoy should serve immediately.
connectRedis().catch(() => {});
