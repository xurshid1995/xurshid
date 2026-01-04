-- Migration: Sale jadvaliga location_id va location_type qo'shish
-- Maqsad: Multi-location savdolar uchun aniq joylashuvni saqlash
-- Sana: 2026-01-04

-- 1. Yangi ustunlarni qo'shish
ALTER TABLE sales ADD COLUMN IF NOT EXISTS location_id INTEGER;
ALTER TABLE sales ADD COLUMN IF NOT EXISTS location_type VARCHAR(20);

-- 2. Mavjud savdolar uchun ma'lumotlarni ko'chirish (store_id -> location_id)
UPDATE sales 
SET location_id = store_id, 
    location_type = 'store'
WHERE store_id IS NOT NULL 
  AND (location_id IS NULL OR location_type IS NULL);

-- 3. Index qo'shish (tezlikni oshirish uchun)
CREATE INDEX IF NOT EXISTS idx_sales_location ON sales(location_id, location_type);

-- 4. Tekshirish
SELECT 
    COUNT(*) as total_sales,
    COUNT(location_id) as with_location,
    COUNT(DISTINCT location_id) as unique_locations,
    location_type
FROM sales
GROUP BY location_type;
