const express = require("express");
const { createClient } = require("redis");

const app = express();
app.use(express.json({ limit: "1mb" }));

const PORT = parseInt(process.env.PORT || "8081", 10);
const REDIS_URL =
  process.env.REDIS_URL || "redis://redis.monitoring.svc.cluster.local:6379";
const REDIS_CHANNEL = "decoy_interaction";
const DECOY_ID = process.env.DECOY_ID || "decoy-api";
const ATTACK_ID = process.env.ATTACK_ID || "";
const ATTACKER_IP = process.env.ATTACKER_IP || "";

let redisClient = null;
let redisReady = false;

async function connectRedis() {
  try {
    redisClient = createClient({ url: REDIS_URL });
    redisClient.on("ready", () => {
      redisReady = true;
    });
    redisClient.on("error", () => {
      redisReady = false;
    });
    await redisClient.connect();
  } catch (_) {
    redisReady = false;
  }
}

async function publishInteraction(req, resCode) {
  if (!redisReady || !redisClient) {
    return;
  }
  const event = {
    timestamp: new Date().toISOString(),
    type: "decoy_interaction",
    decoy_id: DECOY_ID,
    decoy_type: "api",
    attack_id: ATTACK_ID,
    attacker_ip: ATTACKER_IP,
    source_ip: req.ip || req.socket.remoteAddress || "",
    method: req.method,
    path: req.originalUrl,
    headers: req.headers,
    query_params: req.query || {},
    body: req.body || {},
    response_code: resCode,
  };
  try {
    await redisClient.publish(REDIS_CHANNEL, JSON.stringify(event));
  } catch (_) {}
}

function withInteraction(handler) {
  return async (req, res) => {
    try {
      await handler(req, res);
    } finally {
      publishInteraction(req, res.statusCode);
    }
  };
}

const fakeProducts = [
  { id: 101, sku: "TRAP-101", name: "Premium Phone Max", price: 1299.0 },
  { id: 102, sku: "TRAP-102", name: "Ultra Laptop Pro", price: 2499.0 },
  { id: 103, sku: "TRAP-103", name: "Gaming Headset X", price: 349.0 },
];

app.use((_, res, next) => {
  res.setHeader("X-Service-Node", `decoy-api-${DECOY_ID}`);
  next();
});

app.get(
  "/health",
  withInteraction(async (_req, res) => {
    res.json({ status: "healthy", service: "decoy-api", decoy_id: DECOY_ID });
  })
);

app.get(
  "/api/products",
  withInteraction(async (_req, res) => {
    res.json(fakeProducts);
  })
);

app.get(
  "/api/products/:id",
  withInteraction(async (req, res) => {
    const id = parseInt(req.params.id, 10);
    const product = fakeProducts.find((p) => p.id === id);
    if (!product) {
      return res.status(404).json({ error: "Product not found" });
    }
    res.json(product);
  })
);

app.post(
  "/api/cart/add",
  withInteraction(async (req, res) => {
    const quantity = Math.max(1, parseInt(req.body.quantity || 1, 10));
    res.status(201).json({
      status: "ok",
      message: "Item added to cart",
      cart_item_id: Math.floor(Math.random() * 100000),
      quantity,
    });
  })
);

app.post(
  "/api/auth/login",
  withInteraction(async (_req, res) => {
    res.status(401).json({
      error: "Invalid username or password",
      code: "AUTH_FAILED",
    });
  })
);

app.use(
  "*",
  withInteraction(async (req, res) => {
    res.status(404).json({
      error: "Not Found",
      path: req.originalUrl,
    });
  })
);

app.listen(PORT, "0.0.0.0", () => {
  process.stdout.write(
    JSON.stringify({
      timestamp: new Date().toISOString(),
      level: "INFO",
      service: "decoy-api",
      message: `listening on ${PORT}`,
      decoy_id: DECOY_ID,
    }) + "\n"
  );
});

// Do not block startup on Redis connection; decoy should serve immediately.
connectRedis().catch(() => {});
