CREATE TABLE IF NOT EXISTS expenses (
    id SERIAL PRIMARY KEY,
    title VARCHAR(300) NOT NULL,
    amount_usd DECIMAL(15,2) NOT NULL DEFAULT 0,
    amount_uzs DECIMAL(20,2) NOT NULL DEFAULT 0,
    category VARCHAR(100),
    description TEXT,
    expense_date TIMESTAMP DEFAULT NOW(),
    created_by VARCHAR(100),
    location_type VARCHAR(20),
    location_id INTEGER,
    location_name VARCHAR(200),
    created_at TIMESTAMP DEFAULT NOW()
);
