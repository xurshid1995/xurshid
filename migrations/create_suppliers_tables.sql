-- Yetkazib beruvchilar va ular bilan bog'liq kirim/to'lov tarixi
-- ESLATMA: app.py da db.create_all() ishga tushganda bu jadvallar avtomatik yaratiladi,
-- shu fayl faqat hujjatlashtirish va serverda qo'lda tekshirish uchun saqlanadi.

CREATE TABLE IF NOT EXISTS suppliers (
    id SERIAL PRIMARY KEY,
    name VARCHAR(150) NOT NULL,
    phone VARCHAR(20),
    contact_person VARCHAR(100),
    address TEXT,
    notes TEXT,
    balance_usd DECIMAL(15, 2) NOT NULL DEFAULT 0,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS supplier_purchases (
    id SERIAL PRIMARY KEY,
    supplier_id INTEGER REFERENCES suppliers(id) ON DELETE SET NULL,
    product_id INTEGER REFERENCES products(id) ON DELETE SET NULL,
    product_name VARCHAR(200) NOT NULL,
    quantity DECIMAL(15, 3) NOT NULL,
    cost_price DECIMAL(15, 4) NOT NULL,
    total_amount DECIMAL(15, 2) NOT NULL,
    payment_type VARCHAR(20) NOT NULL DEFAULT 'cash',
    paid_amount DECIMAL(15, 2) NOT NULL DEFAULT 0,
    debt_amount DECIMAL(15, 2) NOT NULL DEFAULT 0,
    location_type VARCHAR(20),
    location_name VARCHAR(200),
    added_by VARCHAR(100),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS supplier_payments (
    id SERIAL PRIMARY KEY,
    supplier_id INTEGER REFERENCES suppliers(id) ON DELETE SET NULL,
    amount_usd DECIMAL(15, 2) NOT NULL,
    payment_method VARCHAR(20) DEFAULT 'cash',
    paid_by VARCHAR(100),
    notes TEXT,
    payment_date TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_supplier_purchases_supplier ON supplier_purchases(supplier_id);
CREATE INDEX IF NOT EXISTS idx_supplier_purchases_date ON supplier_purchases(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_supplier_payments_supplier ON supplier_payments(supplier_id);
CREATE INDEX IF NOT EXISTS idx_supplier_payments_date ON supplier_payments(payment_date DESC);
