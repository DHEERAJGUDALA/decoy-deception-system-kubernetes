CREATE TABLE IF NOT EXISTS users (
  id SERIAL PRIMARY KEY,
  username TEXT NOT NULL,
  email TEXT NOT NULL,
  password_hash TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS products (
  id SERIAL PRIMARY KEY,
  sku TEXT NOT NULL,
  name TEXT NOT NULL,
  price NUMERIC(10, 2) NOT NULL
);

CREATE TABLE IF NOT EXISTS orders (
  id SERIAL PRIMARY KEY,
  user_id INT NOT NULL,
  total NUMERIC(10, 2) NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

INSERT INTO users (username, email, password_hash) VALUES
  ('admin', 'admin@techmart.local', '$2b$12$fakehashadmin'),
  ('support', 'support@techmart.local', '$2b$12$fakehashsupport')
ON CONFLICT DO NOTHING;

INSERT INTO products (sku, name, price) VALUES
  ('TRAP-101', 'Premium Phone Max', 1299.00),
  ('TRAP-102', 'Ultra Laptop Pro', 2499.00),
  ('TRAP-103', 'Gaming Headset X', 349.00)
ON CONFLICT DO NOTHING;
