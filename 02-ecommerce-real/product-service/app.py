"""
Product Service — Flask REST API for the real e-commerce storefront.

Serves product catalog data from PostgreSQL. Every response includes
an X-Service-Node header so monitoring can distinguish real vs decoy traffic.
All requests are logged to stdout in JSON format for structured log ingestion.
"""

import json
import logging
import os
import sys
import time
from datetime import datetime, timezone

import psycopg2
import psycopg2.pool
from flask import Flask, g, jsonify, request

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------
app = Flask(__name__)


# ---------------------------------------------------------------------------
# Structured JSON logging to stdout
# ---------------------------------------------------------------------------
class JsonFormatter(logging.Formatter):
    def format(self, record):
        log_record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "message": record.getMessage(),
        }
        return json.dumps(log_record)


handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(JsonFormatter())
app.logger.handlers = [handler]
app.logger.setLevel(logging.INFO)
# Suppress default werkzeug request logs (we log our own)
logging.getLogger("werkzeug").setLevel(logging.WARNING)

# ---------------------------------------------------------------------------
# Database connection pool (max 3 connections for low-resource system)
# ---------------------------------------------------------------------------
DB_POOL = None


def get_db_pool():
    """Lazily initialise the connection pool on first use."""
    global DB_POOL
    if DB_POOL is None:
        DB_POOL = psycopg2.pool.SimpleConnectionPool(
            minconn=1,
            maxconn=3,
            host=os.environ.get("DB_HOST", "postgres"),
            port=int(os.environ.get("DB_PORT", "5432")),
            dbname=os.environ.get("DB_NAME", "ecommerce"),
            user=os.environ.get("DB_USER", "appuser"),
            password=os.environ.get("DB_PASSWORD", ""),
        )
    return DB_POOL


def get_db():
    """Get a connection from the pool, stored on Flask's `g` for the request."""
    if "db" not in g:
        pool = get_db_pool()
        g.db = pool.getconn()
    return g.db


@app.teardown_appcontext
def return_db(exc):
    """Return the connection to the pool at the end of the request."""
    db = g.pop("db", None)
    if db is not None:
        try:
            pool = get_db_pool()
            pool.putconn(db)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Middleware: custom header + request logging
# ---------------------------------------------------------------------------
@app.before_request
def start_timer():
    g.start_time = time.monotonic()


@app.after_request
def after_request(response):
    # Tag every response so monitoring can tell real from decoy
    response.headers["X-Service-Node"] = "real-product-svc"

    # Structured request log
    duration_ms = round(
        (time.monotonic() - g.get("start_time", time.monotonic())) * 1000, 2
    )
    log_entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "method": request.method,
        "path": request.path,
        "source_ip": request.remote_addr,
        "user_agent": request.headers.get("User-Agent", ""),
        "response_code": response.status_code,
        "duration_ms": duration_ms,
    }
    app.logger.info(json.dumps(log_entry))
    return response


# ---------------------------------------------------------------------------
# Helper: serialise a product row to dict
# ---------------------------------------------------------------------------
PRODUCT_COLUMNS = (
    "id",
    "name",
    "description",
    "price",
    "image_url",
    "category",
    "stock_count",
)


def row_to_dict(row):
    d = dict(zip(PRODUCT_COLUMNS, row))
    d["price"] = float(d["price"])  # Decimal → float for JSON
    return d


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.route("/health")
def health():
    return jsonify({"status": "healthy", "service": "product-service"})


@app.route("/api/products")
def list_products():
    try:
        conn = get_db()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, name, description, price, image_url, category, stock_count FROM products ORDER BY id"
            )
            rows = cur.fetchall()
        return jsonify([row_to_dict(r) for r in rows])
    except psycopg2.Error as e:
        app.logger.error(f"Database error: {e}")
        return jsonify({"error": "Service temporarily unavailable"}), 503


@app.route("/api/products/<id>")
def get_product(id):
    # Input validation: reject non-numeric IDs
    if not id.isdigit():
        return jsonify({"error": "Invalid product ID — must be numeric"}), 400

    try:
        conn = get_db()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, name, description, price, image_url, category, stock_count FROM products WHERE id = %s",
                (int(id),),
            )
            row = cur.fetchone()
        if row is None:
            return jsonify({"error": "Product not found"}), 404
        return jsonify(row_to_dict(row))
    except psycopg2.Error as e:
        app.logger.error(f"Database error: {e}")
        return jsonify({"error": "Service temporarily unavailable"}), 503


@app.route("/api/products/category/<category>")
def products_by_category(category):
    try:
        conn = get_db()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, name, description, price, image_url, category, stock_count FROM products WHERE category = %s ORDER BY id",
                (category,),
            )
            rows = cur.fetchall()
        return jsonify([row_to_dict(r) for r in rows])
    except psycopg2.Error as e:
        app.logger.error(f"Database error: {e}")
        return jsonify({"error": "Service temporarily unavailable"}), 503


# ---------------------------------------------------------------------------
# Dev server (not used in production — gunicorn is the entrypoint)
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8081, debug=False)
