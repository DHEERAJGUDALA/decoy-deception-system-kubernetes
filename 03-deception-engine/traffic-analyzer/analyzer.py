"""
Traffic Analyzer — Flask service for real-time HTTP request analysis.

Receives mirrored request metadata from the traffic-router, runs all
detection methods from AttackDetector, and publishes attack events to
Redis pub/sub. Part of the deception-gateway namespace.

Endpoints:
    POST /analyze        — Analyze a request, return verdict + publish if attack
    GET  /stats          — Detection statistics (counts per attack type)
    GET  /recent-attacks — Last 100 detected attacks
    GET  /health         — Health check
"""

import json
import logging
import os
import sys
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timezone

import redis
from flask import Flask, g, jsonify, request

from attack_patterns import AttackDetector

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------
app = Flask(__name__)


# ---------------------------------------------------------------------------
# Structured JSON logging to stdout (same pattern as product-service)
# ---------------------------------------------------------------------------
class JsonFormatter(logging.Formatter):
    def format(self, record):
        log_record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "service": "traffic-analyzer",
            "message": record.getMessage(),
        }
        return json.dumps(log_record)


handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(JsonFormatter())
app.logger.handlers = [handler]
app.logger.setLevel(logging.INFO)
logging.getLogger("werkzeug").setLevel(logging.WARNING)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
REDIS_URL = os.environ.get(
    "REDIS_URL", "redis://redis.monitoring.svc.cluster.local:6379"
)
REDIS_CHANNEL = "attack_detected"
CONFIDENCE_THRESHOLD = float(os.environ.get("CONFIDENCE_THRESHOLD", "0.6"))
PORT = int(os.environ.get("PORT", "8085"))

# Maximum number of recent attacks to keep in memory
MAX_RECENT_ATTACKS = 100

# Cleanup interval for stale rate-tracking state (seconds)
CLEANUP_INTERVAL = 60

# ---------------------------------------------------------------------------
# Shared state (thread-safe via GIL for simple operations)
# ---------------------------------------------------------------------------
detector = AttackDetector()

# Statistics counters
stats = {
    "total_analyzed": 0,
    "total_attacks_detected": 0,
    "attacks_by_type": defaultdict(int),
    "started_at": datetime.now(timezone.utc).isoformat(),
}
stats_lock = threading.Lock()

# Circular buffer for recent attacks
recent_attacks = deque(maxlen=MAX_RECENT_ATTACKS)
recent_lock = threading.Lock()

# ---------------------------------------------------------------------------
# Redis client (non-blocking — if Redis is down, analysis still works)
# ---------------------------------------------------------------------------
redis_client = None


def get_redis():
    """Lazily connect to Redis. Returns None if unavailable."""
    global redis_client
    if redis_client is not None:
        return redis_client
    try:
        redis_client = redis.from_url(
            REDIS_URL,
            socket_connect_timeout=3,
            socket_timeout=2,
            retry_on_timeout=False,
        )
        # Verify connection
        redis_client.ping()
        app.logger.info(f"Connected to Redis at {REDIS_URL}")
        return redis_client
    except redis.RedisError as e:
        app.logger.warning(f"Redis unavailable: {e}")
        redis_client = None
        return None


def publish_attack(attack_event):
    """Publish an attack event to Redis. Fails silently."""
    client = get_redis()
    if client is None:
        return
    try:
        client.publish(REDIS_CHANNEL, json.dumps(attack_event))
    except redis.RedisError as e:
        app.logger.warning(f"Redis publish failed: {e}")
        # Reset client so next call retries connection
        global redis_client
        redis_client = None


# ---------------------------------------------------------------------------
# Background cleanup thread — prevents unbounded memory from rate tracking
# ---------------------------------------------------------------------------
def _cleanup_loop():
    """Periodically clean up stale rate-tracking state."""
    while True:
        time.sleep(CLEANUP_INTERVAL)
        try:
            detector.cleanup_stale_state(max_age=120.0)
        except Exception:
            pass  # never crash the cleanup thread


_cleanup_thread = threading.Thread(target=_cleanup_loop, daemon=True)
_cleanup_thread.start()


# ---------------------------------------------------------------------------
# Middleware: request timing + X-Service-Node header
# ---------------------------------------------------------------------------
@app.before_request
def start_timer():
    g.start_time = time.monotonic()


@app.after_request
def after_request(response):
    response.headers["X-Service-Node"] = "traffic-analyzer"

    duration_ms = round(
        (time.monotonic() - g.get("start_time", time.monotonic())) * 1000, 2
    )
    log_entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "method": request.method,
        "path": request.path,
        "source_ip": request.remote_addr,
        "response_code": response.status_code,
        "duration_ms": duration_ms,
    }
    app.logger.info(json.dumps(log_entry))
    return response


# ---------------------------------------------------------------------------
# POST /analyze — Core analysis endpoint
# ---------------------------------------------------------------------------
@app.route("/analyze", methods=["POST"])
def analyze():
    """
    Analyze mirrored HTTP request metadata for attacks.

    Expects JSON body:
    {
        "method": "GET",
        "path": "/api/products?id=1' OR 1=1--",
        "headers": {"User-Agent": "...", ...},
        "body": "...",
        "source_ip": "192.168.1.100",
        "query_params": {"id": "1' OR 1=1--"},
        "timestamp": "2024-01-15T10:30:00Z"
    }

    Returns:
    {
        "attack": true/false,
        "type": "sqli" | null,
        "confidence": 0.95 | null,
        "action": "redirect_to_decoy" | "allow",
        "findings_count": 3,
        "top_finding": {...}
    }
    """
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Request body must be valid JSON"}), 400

    # Validate required fields
    if "method" not in data or "path" not in data:
        return jsonify({"error": "Missing required fields: method, path"}), 400

    # Run all detections
    findings = detector.analyze(data)

    # Update stats
    with stats_lock:
        stats["total_analyzed"] += 1

    # Filter findings above confidence threshold
    high_confidence = [f for f in findings if f["confidence"] > CONFIDENCE_THRESHOLD]

    if high_confidence:
        # Sort by confidence descending — the top finding drives the response
        high_confidence.sort(key=lambda f: f["confidence"], reverse=True)
        top = high_confidence[0]

        # Build the attack event for Redis
        attack_event = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "type": "attack_detected",
            "attack_type": top["attack_type"],
            "confidence": top["confidence"],
            "source_ip": top["source_ip"],
            "evidence": top["evidence"],
            "findings_count": len(high_confidence),
            "all_findings": high_confidence,
            "request": {
                "method": data.get("method"),
                "path": data.get("path"),
                "source_ip": data.get("source_ip"),
                "user_agent": data.get("headers", {}).get("User-Agent", ""),
            },
        }

        # Publish to Redis
        publish_attack(attack_event)

        # Update stats
        with stats_lock:
            stats["total_attacks_detected"] += 1
            for f in high_confidence:
                stats["attacks_by_type"][f["attack_type"]] += 1

        # Store in recent attacks buffer
        with recent_lock:
            recent_attacks.append(attack_event)

        return jsonify(
            {
                "attack": True,
                "type": top["attack_type"],
                "confidence": top["confidence"],
                "action": "redirect_to_decoy",
                "findings_count": len(high_confidence),
                "top_finding": top,
            }
        )

    return jsonify(
        {
            "attack": False,
            "type": None,
            "confidence": None,
            "action": "allow",
            "findings_count": 0,
            "top_finding": None,
        }
    )


# ---------------------------------------------------------------------------
# GET /stats — Detection statistics
# ---------------------------------------------------------------------------
@app.route("/stats")
def get_stats():
    """Return aggregate detection statistics."""
    with stats_lock:
        tracking = detector.get_tracking_stats()
        return jsonify(
            {
                "total_analyzed": stats["total_analyzed"],
                "total_attacks_detected": stats["total_attacks_detected"],
                "attacks_by_type": dict(stats["attacks_by_type"]),
                "detection_rate": (
                    round(stats["total_attacks_detected"] / stats["total_analyzed"], 4)
                    if stats["total_analyzed"] > 0
                    else 0.0
                ),
                "confidence_threshold": CONFIDENCE_THRESHOLD,
                "started_at": stats["started_at"],
                "uptime_seconds": round(
                    (
                        datetime.now(timezone.utc)
                        - datetime.fromisoformat(stats["started_at"])
                    ).total_seconds()
                ),
                "tracking_state": tracking,
            }
        )


# ---------------------------------------------------------------------------
# GET /recent-attacks — Last N detected attacks
# ---------------------------------------------------------------------------
@app.route("/recent-attacks")
def get_recent_attacks():
    """Return the last 100 detected attacks (newest first)."""
    with recent_lock:
        attacks = list(recent_attacks)
    # Return newest first
    attacks.reverse()
    return jsonify(
        {
            "count": len(attacks),
            "max_stored": MAX_RECENT_ATTACKS,
            "attacks": attacks,
        }
    )


# ---------------------------------------------------------------------------
# GET /health — Health check
# ---------------------------------------------------------------------------
@app.route("/health")
def health():
    """Health check endpoint for Kubernetes probes."""
    redis_ok = False
    try:
        client = get_redis()
        if client:
            client.ping()
            redis_ok = True
    except Exception:
        pass

    return jsonify(
        {
            "status": "healthy",
            "service": "traffic-analyzer",
            "redis_connected": redis_ok,
            "total_analyzed": stats["total_analyzed"],
        }
    )


# ---------------------------------------------------------------------------
# Dev server (not used in production — gunicorn is the entrypoint)
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT, debug=False)
