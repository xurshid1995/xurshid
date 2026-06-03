-- Migration: Add missing foreign-key indexes
-- Purpose: Index FK columns that are filtered/joined but had no index
-- Date: 2026-06-03
-- Safe: uses IF NOT EXISTS, can be run repeatedly

-- customers.store_id - do'kon bo'yicha mijozlarni filtrlash uchun
CREATE INDEX IF NOT EXISTS idx_customers_store_id ON customers(store_id);

-- sales.store_id - do'kon bo'yicha sotuvlar uchun
CREATE INDEX IF NOT EXISTS idx_sales_store_id ON sales(store_id);

-- sales.seller_id - sotuvchi bo'yicha sotuvlar/hisobotlar uchun
CREATE INDEX IF NOT EXISTS idx_sales_seller_id ON sales(seller_id);

-- Statistikani yangilash
ANALYZE customers;
ANALYZE sales;
