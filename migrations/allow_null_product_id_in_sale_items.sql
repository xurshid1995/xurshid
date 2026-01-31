-- Migration: Allow NULL product_id in sale_items table
-- Purpose: When a product is deleted from warehouse/store, keep sale history but set product_id to NULL
-- Date: 2026-01-31

-- Make product_id nullable in sale_items table
ALTER TABLE sale_items 
ALTER COLUMN product_id DROP NOT NULL;

-- Add comment for documentation
COMMENT ON COLUMN sale_items.product_id IS 'Product ID - NULL if product was deleted from system';
