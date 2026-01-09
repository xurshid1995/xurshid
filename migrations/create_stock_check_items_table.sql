-- Stock check items jadvali yaratish
CREATE TABLE IF NOT EXISTS stock_check_items (
    id SERIAL PRIMARY KEY,
    session_id INTEGER NOT NULL REFERENCES stock_check_sessions(id) ON DELETE CASCADE,
    product_id INTEGER NOT NULL REFERENCES products(id),
    product_name VARCHAR(200),
    system_quantity DECIMAL(10, 2) NOT NULL,
    actual_quantity DECIMAL(10, 2) NOT NULL,
    difference DECIMAL(10, 2),
    status VARCHAR(20),
    checked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(session_id, product_id)
);

-- Indexlar qo'shish
CREATE INDEX IF NOT EXISTS idx_stock_check_items_session ON stock_check_items(session_id);
CREATE INDEX IF NOT EXISTS idx_stock_check_items_product ON stock_check_items(product_id);
CREATE INDEX IF NOT EXISTS idx_stock_check_items_status ON stock_check_items(status);
