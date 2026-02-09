"""
Cart Service — Flask REST API for shopping cart and checkout.

Manages session-based carts backed by PostgreSQL. Every response includes
an X-Service-Node header so monitoring can distinguish real vs decoy traffic.
All requests are logged to stdout in JSON format for structured log ingestion.
"""

import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal

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
    response.headers["X-Service-Node"] = "real-cart-svc"

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
# Input validation helpers
# ---------------------------------------------------------------------------
SESSION_RE = re.compile(r"^[a-zA-Z0-9_-]+$")


def validate_session_id(session_id):
    """Return an error response tuple if invalid, else None."""
    if not session_id or not SESSION_RE.match(session_id):
        return jsonify(
            {
                "error": "Invalid session_id — must be alphanumeric (hyphens/underscores allowed)"
            }
        ), 400
    return None


def validate_int_param(value, name, min_val=None, max_val=None):
    """Return (parsed_int, None) on success or (None, error_response) on failure."""
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None, (jsonify({"error": f"Invalid {name} — must be an integer"}), 400)
    if min_val is not None and parsed < min_val:
        return None, (jsonify({"error": f"{name} must be at least {min_val}"}), 400)
    if max_val is not None and parsed > max_val:
        return None, (jsonify({"error": f"{name} must be at most {max_val}"}), 400)
    return parsed, None


# ---------------------------------------------------------------------------
# Helper: serialise a cart row (with product JOIN) to dict
# ---------------------------------------------------------------------------
CART_COLUMNS = (
    "cart_item_id",
    "quantity",
    "added_at",
    "product_id",
    "name",
    "description",
    "price",
    "image_url",
    "category",
)


def cart_row_to_dict(row):
    d = dict(zip(CART_COLUMNS, row))
    d["price"] = float(d["price"])
    d["added_at"] = d["added_at"].isoformat()
    return d


# ---------------------------------------------------------------------------
# SQL: cart items with product details
# ---------------------------------------------------------------------------
CART_SELECT_SQL = """
    SELECT c.id, c.quantity, c.added_at,
           p.id, p.name, p.description, p.price, p.image_url, p.category
    FROM cart_items c
    JOIN products p ON c.product_id = p.id
    WHERE c.session_id = %s
    ORDER BY c.added_at
"""


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.route("/health")
def health():
    return jsonify({"status": "healthy", "service": "cart-service"})


@app.route("/api/cart/add", methods=["POST"])
def add_to_cart():
    """Add an item to the session cart. Returns the full updated cart."""
    body = request.get_json(silent=True)
    if not body:
        return jsonify({"error": "Request body must be JSON"}), 400

    session_id = body.get("session_id", "")
    err = validate_session_id(session_id)
    if err:
        return err

    product_id, err = validate_int_param(
        body.get("product_id"), "product_id", min_val=1
    )
    if err:
        return err

    quantity, err = validate_int_param(
        body.get("quantity", 1), "quantity", min_val=1, max_val=99
    )
    if err:
        return err

    try:
        conn = get_db()
        with conn.cursor() as cur:
            # Verify product exists and has stock
            cur.execute(
                "SELECT id, stock_count FROM products WHERE id = %s", (product_id,)
            )
            product = cur.fetchone()
            if product is None:
                return jsonify({"error": "Product not found"}), 404
            if product[1] < quantity:
                return jsonify({"error": "Insufficient stock"}), 400

            # Check if item already in cart for this session — update quantity
            cur.execute(
                "SELECT id, quantity FROM cart_items WHERE session_id = %s AND product_id = %s",
                (session_id, product_id),
            )
            existing = cur.fetchone()
            if existing:
                new_qty = existing[1] + quantity
                if new_qty > 99:
                    new_qty = 99
                cur.execute(
                    "UPDATE cart_items SET quantity = %s WHERE id = %s",
                    (new_qty, existing[0]),
                )
            else:
                cur.execute(
                    "INSERT INTO cart_items (session_id, product_id, quantity) VALUES (%s, %s, %s)",
                    (session_id, product_id, quantity),
                )
            conn.commit()

            # Return updated cart
            cur.execute(CART_SELECT_SQL, (session_id,))
            rows = cur.fetchall()

        return jsonify(
            {"session_id": session_id, "items": [cart_row_to_dict(r) for r in rows]}
        ), 201
    except psycopg2.Error as e:
        app.logger.error(f"Database error: {e}")
        try:
            conn.rollback()
        except Exception:
            pass
        return jsonify({"error": "Service temporarily unavailable"}), 500


@app.route("/api/cart/<session_id>")
def get_cart(session_id):
    """Return all cart items for a session with product details."""
    err = validate_session_id(session_id)
    if err:
        return err

    try:
        conn = get_db()
        with conn.cursor() as cur:
            cur.execute(CART_SELECT_SQL, (session_id,))
            rows = cur.fetchall()
        return jsonify(
            {"session_id": session_id, "items": [cart_row_to_dict(r) for r in rows]}
        )
    except psycopg2.Error as e:
        app.logger.error(f"Database error: {e}")
        return jsonify({"error": "Service temporarily unavailable"}), 500


@app.route("/api/cart/<session_id>/<item_id>", methods=["DELETE"])
def remove_from_cart(session_id, item_id):
    """Remove a specific item from the cart."""
    err = validate_session_id(session_id)
    if err:
        return err

    item_id_int, err = validate_int_param(item_id, "item_id", min_val=1)
    if err:
        return err

    try:
        conn = get_db()
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM cart_items WHERE id = %s AND session_id = %s RETURNING id",
                (item_id_int, session_id),
            )
            deleted = cur.fetchone()
            conn.commit()

        if deleted is None:
            return jsonify({"error": "Cart item not found"}), 404
        return jsonify({"message": "Item removed", "deleted_item_id": item_id_int})
    except psycopg2.Error as e:
        app.logger.error(f"Database error: {e}")
        try:
            conn.rollback()
        except Exception:
            pass
        return jsonify({"error": "Service temporarily unavailable"}), 500


@app.route("/api/cart/<session_id>/checkout", methods=["POST"])
def checkout(session_id):
    """Create an order from the current cart, clear the cart, return confirmation."""
    err = validate_session_id(session_id)
    if err:
        return err

    try:
        conn = get_db()
        with conn.cursor() as cur:
            # Fetch cart with prices for total calculation
            cur.execute(
                """
                SELECT c.id, c.product_id, c.quantity, p.price
                FROM cart_items c
                JOIN products p ON c.product_id = p.id
                WHERE c.session_id = %s
                """,
                (session_id,),
            )
            cart_rows = cur.fetchall()

            if not cart_rows:
                return jsonify({"error": "Cart is empty"}), 400

            # Calculate total
            total = sum(Decimal(str(row[2])) * row[3] for row in cart_rows)

            # Create order
            cur.execute(
                "INSERT INTO orders (session_id, total_price, status) VALUES (%s, %s, %s) RETURNING id, created_at",
                (session_id, total, "confirmed"),
            )
            order_row = cur.fetchone()
            order_id = order_row[0]
            created_at = order_row[1]

            # Decrement stock for each product
            for row in cart_rows:
                cur.execute(
                    "UPDATE products SET stock_count = stock_count - %s WHERE id = %s AND stock_count >= %s",
                    (row[2], row[1], row[2]),
                )

            # Clear the cart
            cur.execute("DELETE FROM cart_items WHERE session_id = %s", (session_id,))
            conn.commit()

        return jsonify(
            {
                "order_id": order_id,
                "session_id": session_id,
                "total_price": float(total),
                "status": "confirmed",
                "created_at": created_at.isoformat(),
                "items_count": len(cart_rows),
            }
        ), 201
    except psycopg2.Error as e:
        app.logger.error(f"Database error: {e}")
        try:
            conn.rollback()
        except Exception:
            pass
        return jsonify({"error": "Service temporarily unavailable"}), 500


# ---------------------------------------------------------------------------
# Dev server (not used in production — gunicorn is the entrypoint)
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8082, debug=False)
