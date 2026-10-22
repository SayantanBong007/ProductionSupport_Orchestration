-- Run as postgres superuser (POSTGRES_USER=postgres)

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- The application role and its grants are created by 02-app-user.sh from env vars.

-- Schema
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    email VARCHAR(255) UNIQUE NOT NULL,
    name VARCHAR(255) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS orders (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    product_name VARCHAR(255) NOT NULL,
    quantity INTEGER NOT NULL DEFAULT 1,
    price INTEGER NOT NULL,
    status VARCHAR(50) DEFAULT 'pending',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_orders_user_id ON orders(user_id);
CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);

-- Seed data
INSERT INTO users (email, name) VALUES
    ('alice@example.com', 'Alice Johnson'),
    ('bob@example.com', 'Bob Smith'),
    ('carol@example.com', 'Carol Williams'),
    ('david@example.com', 'David Brown'),
    ('eve@example.com', 'Eve Davis')
ON CONFLICT (email) DO NOTHING;

INSERT INTO orders (user_id, product_name, quantity, price, status) VALUES
    (1, 'Laptop', 1, 99900, 'delivered'),
    (1, 'Mouse', 2, 2500, 'delivered'),
    (2, 'Keyboard', 1, 7500, 'shipped'),
    (3, 'Monitor', 1, 29900, 'pending'),
    (4, 'Headphones', 1, 15000, 'pending'),
    (2, 'USB Hub', 1, 3500, 'delivered'),
    (3, 'Desk Lamp', 1, 4500, 'shipped')
ON CONFLICT DO NOTHING;
