-- Qarz to'lovlari tarixini saqlash uchun jadval
CREATE TABLE IF NOT EXISTS debt_payments (
    id SERIAL PRIMARY KEY,
    customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    sale_id INTEGER REFERENCES sales(id) ON DELETE SET NULL,
    payment_date TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    cash_usd DECIMAL(12, 2) DEFAULT 0,
    click_usd DECIMAL(12, 2) DEFAULT 0,
    terminal_usd DECIMAL(12, 2) DEFAULT 0,
    total_usd DECIMAL(12, 2) NOT NULL,
    currency_rate DECIMAL(15, 4) NOT NULL DEFAULT 12500,
    received_by VARCHAR(100) NOT NULL,
    notes TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Index qo'shish tezroq qidiruv uchun
CREATE INDEX IF NOT EXISTS idx_debt_payments_customer ON debt_payments(customer_id);
CREATE INDEX IF NOT EXISTS idx_debt_payments_date ON debt_payments(payment_date DESC);
CREATE INDEX IF NOT EXISTS idx_debt_payments_received_by ON debt_payments(received_by);
