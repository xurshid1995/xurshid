-- Migration: Add performance indexes
-- Purpose: Speed up common queries on sales, sale_items, stocks
-- Date: 2026-05-24

-- sale_items indexes
CREATE INDEX IF NOT EXISTS idx_sale_items_sale_id ON sale_items(sale_id);
CREATE INDEX IF NOT EXISTS idx_sale_items_product_id ON sale_items(product_id);
CREATE INDEX IF NOT EXISTS idx_sale_items_source ON sale_items(source_type, source_id);

-- sales indexes
CREATE INDEX IF NOT EXISTS idx_sales_sale_date ON sales(sale_date);
CREATE INDEX IF NOT EXISTS idx_sales_customer_id ON sales(customer_id);
CREATE INDEX IF NOT EXISTS idx_sales_payment_status ON sales(payment_status);
CREATE INDEX IF NOT EXISTS idx_sales_location ON sales(location_id, location_type);
CREATE INDEX IF NOT EXISTS idx_sales_created_at ON sales(created_at);

-- warehouse_stocks composite index
CREATE INDEX IF NOT EXISTS idx_warehouse_stocks_wh_prod ON warehouse_stocks(warehouse_id, product_id);

-- store_stocks composite index
CREATE INDEX IF NOT EXISTS idx_store_stocks_store_prod ON store_stocks(store_id, product_id);

-- users reset_token index (for password reset lookup)
CREATE INDEX IF NOT EXISTS idx_users_reset_token ON users(reset_token) WHERE reset_token IS NOT NULL;
