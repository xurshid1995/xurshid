-- Mahsulot qo'shilgan tarix jadvali - faqat ma'lumot uchun
CREATE TABLE IF NOT EXISTS product_add_history (
    id SERIAL PRIMARY KEY,
    product_name VARCHAR(200) NOT NULL,
    cost_price DECIMAL(15, 2) NOT NULL,
    sell_price DECIMAL(15, 2) NOT NULL,
    quantity DECIMAL(15, 3) NOT NULL,
    location_type VARCHAR(20) NOT NULL,  -- 'warehouse' or 'store'
    location_name VARCHAR(200) NOT NULL,  -- Ombor yoki do'kon nomi
    added_by VARCHAR(100),  -- Qo'shgan foydalanuvchi
    added_date TIMESTAMP WITH TIME ZONE DEFAULT (NOW() AT TIME ZONE 'UTC'),
    notes TEXT
);

-- Indexes for better performance
CREATE INDEX IF NOT EXISTS idx_product_add_history_date ON product_add_history(added_date DESC);
CREATE INDEX IF NOT EXISTS idx_product_add_history_name ON product_add_history(product_name);
CREATE INDEX IF NOT EXISTS idx_product_add_history_location ON product_add_history(location_type, location_name);

-- Test query
SELECT * FROM product_add_history ORDER BY added_date DESC LIMIT 10;
