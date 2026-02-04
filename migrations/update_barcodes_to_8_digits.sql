-- 6 xonali barcode'larni 8 xonali qilish (oldiga 00 qo'shish)
-- Masalan: 123456 -> 00123456

-- 1. Avval mavjud barcode'larni ko'rish
SELECT 
    id,
    name,
    barcode,
    LENGTH(barcode) as barcode_length
FROM products 
WHERE barcode IS NOT NULL 
  AND barcode != ''
  AND LENGTH(barcode) = 6
ORDER BY barcode;

-- 2. 6 xonali barcode'larni 8 xonali qilish
UPDATE products 
SET barcode = LPAD(barcode, 8, '0')
WHERE barcode IS NOT NULL 
  AND barcode != ''
  AND LENGTH(barcode) = 6;

-- 3. Yangilangan barcode'larni tekshirish
SELECT 
    id,
    name,
    barcode,
    LENGTH(barcode) as barcode_length
FROM products 
WHERE barcode IS NOT NULL 
  AND barcode != ''
ORDER BY barcode
LIMIT 20;

-- 4. Barcha barcode uzunliklarini ko'rish
SELECT 
    LENGTH(barcode) as barcode_length,
    COUNT(*) as count
FROM products 
WHERE barcode IS NOT NULL 
  AND barcode != ''
GROUP BY LENGTH(barcode)
ORDER BY barcode_length;
