-- =====================================================
-- Kategoriya jadvali va mahsulotga rasm + kategoriya
-- =====================================================

-- 1. Kategoriyalar jadvali
CREATE TABLE IF NOT EXISTS categories (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL UNIQUE,
    color VARCHAR(7) DEFAULT '#6366f1',
    created_at TIMESTAMP DEFAULT NOW()
);

-- 2. Products jadvaliga category_id qo'shish
ALTER TABLE products
    ADD COLUMN IF NOT EXISTS category_id INTEGER REFERENCES categories(id) ON DELETE SET NULL;

-- 3. Products jadvaliga image_path qo'shish
ALTER TABLE products
    ADD COLUMN IF NOT EXISTS image_path VARCHAR(255) DEFAULT NULL;

-- 4. Index qo'shish
CREATE INDEX IF NOT EXISTS idx_products_category_id ON products(category_id);

-- 5. Bir nechta default kategoriyalar (ixtiyoriy)
INSERT INTO categories (name, color) VALUES
    ('Umumiy', '#6366f1'),
    ('Oziq-ovqat', '#22c55e'),
    ('Elektronika', '#3b82f6'),
    ('Kiyim', '#f59e0b'),
    ('Uy-ro''zg''or', '#ef4444')
ON CONFLICT (name) DO NOTHING;
