-- ============================================================================
-- Deception System — E-Commerce Database Initialization
-- ============================================================================
-- Creates the schema for a lightweight demo e-commerce app:
--   - products: catalog with 12 seeded items across 3 categories
--   - cart_items: per-session shopping cart (no auth required)
--   - orders: completed order records
-- Runs automatically on first container start via /docker-entrypoint-initdb.d/
-- ============================================================================

-- ---------------------------------------------------------------------------
-- Products table: the main product catalog
-- ---------------------------------------------------------------------------
CREATE TABLE products (
    id          SERIAL PRIMARY KEY,
    name        VARCHAR(255) NOT NULL,
    description TEXT,
    price       DECIMAL(10, 2) NOT NULL CHECK (price >= 0),
    image_url   VARCHAR(512),
    category    VARCHAR(100) NOT NULL,
    stock_count INTEGER NOT NULL DEFAULT 0 CHECK (stock_count >= 0)
);

-- ---------------------------------------------------------------------------
-- Cart items: session-based shopping cart (no user auth needed)
-- ---------------------------------------------------------------------------
-- session_id is a random client-generated token stored in localStorage.
-- Foreign key to products ensures referential integrity.
CREATE TABLE cart_items (
    id         SERIAL PRIMARY KEY,
    session_id VARCHAR(255) NOT NULL,
    product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    quantity   INTEGER NOT NULL DEFAULT 1 CHECK (quantity > 0),
    added_at   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- ---------------------------------------------------------------------------
-- Orders: completed purchases
-- ---------------------------------------------------------------------------
CREATE TABLE orders (
    id          SERIAL PRIMARY KEY,
    session_id  VARCHAR(255) NOT NULL,
    total_price DECIMAL(10, 2) NOT NULL CHECK (total_price >= 0),
    status      VARCHAR(50) NOT NULL DEFAULT 'pending',
    created_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- ---------------------------------------------------------------------------
-- Indexes for query performance
-- ---------------------------------------------------------------------------
-- Cart lookups are always by session_id (fetch current cart)
CREATE INDEX idx_cart_items_session_id ON cart_items(session_id);

-- Product lookups in cart joins
CREATE INDEX idx_cart_items_product_id ON cart_items(product_id);

-- Order history by session
CREATE INDEX idx_orders_session_id ON orders(session_id);

-- Product filtering by category
CREATE INDEX idx_products_category ON products(category);

-- ---------------------------------------------------------------------------
-- Seed data: 12 realistic products across 3 categories
-- ---------------------------------------------------------------------------
INSERT INTO products (name, description, price, image_url, category, stock_count) VALUES
    -- Electronics (4 products)
    ('Wireless Noise-Canceling Headphones',
     'Over-ear Bluetooth headphones with 30-hour battery life, active noise cancellation, and built-in microphone for calls.',
     79.99, '/images/products/headphones.jpg', 'electronics', 45),

    ('USB-C Hub 7-in-1',
     'Compact adapter with HDMI 4K output, 3x USB-A 3.0, SD card reader, and 100W pass-through charging.',
     34.99, '/images/products/usb-hub.jpg', 'electronics', 120),

    ('Mechanical Keyboard TKL',
     'Tenkeyless mechanical keyboard with Cherry MX Brown switches, per-key RGB lighting, and detachable USB-C cable.',
     89.99, '/images/products/keyboard.jpg', 'electronics', 30),

    ('Portable Bluetooth Speaker',
     'IPX7 waterproof speaker with 360-degree sound, 12-hour battery, and built-in carabiner clip.',
     49.99, '/images/products/speaker.jpg', 'electronics', 75),

    -- Clothing (4 products)
    ('Classic Fit Cotton T-Shirt',
     'Premium 100% organic cotton crew-neck tee. Pre-shrunk, tagless comfort. Available in 8 colors.',
     24.99, '/images/products/tshirt.jpg', 'clothing', 200),

    ('Slim Fit Stretch Chinos',
     'Comfortable stretch-woven chinos with a modern slim fit. Wrinkle-resistant fabric for travel.',
     54.99, '/images/products/chinos.jpg', 'clothing', 85),

    ('Lightweight Rain Jacket',
     'Packable waterproof jacket with sealed seams, adjustable hood, and reflective accents for visibility.',
     69.99, '/images/products/jacket.jpg', 'clothing', 60),

    ('Merino Wool Beanie',
     'Temperature-regulating merino wool beanie. Naturally odor-resistant, breathable, and itch-free.',
     29.99, '/images/products/beanie.jpg', 'clothing', 150),

    -- Books (4 products)
    ('Clean Code: A Handbook of Agile Software Craftsmanship',
     'Robert C. Martin''s classic guide to writing readable, maintainable, and efficient code. Paperback edition.',
     36.99, '/images/products/clean-code.jpg', 'books', 40),

    ('Designing Data-Intensive Applications',
     'Martin Kleppmann''s comprehensive guide to the principles behind reliable, scalable, and maintainable systems.',
     42.99, '/images/products/ddia.jpg', 'books', 35),

    ('The Pragmatic Programmer (20th Anniversary Edition)',
     'Updated classic by David Thomas and Andrew Hunt covering software development best practices and career advice.',
     39.99, '/images/products/pragmatic.jpg', 'books', 55),

    ('Kubernetes in Action (2nd Edition)',
     'Practical guide to deploying and managing containerized applications on Kubernetes. Covers K8s internals and patterns.',
     49.99, '/images/products/k8s-book.jpg', 'books', 25);
