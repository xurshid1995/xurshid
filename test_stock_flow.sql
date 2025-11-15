-- Test uchun stock o'zgarishlarini kuzatish

-- 1. Mahsulotni topish (sidena chixol - ID 5070)
SELECT 
    p.id,
    p.name,
    ss.store_id,
    s.name as store_name,
    ss.quantity as current_stock,
    ss.last_updated
FROM products p
LEFT JOIN store_stocks ss ON p.id = ss.product_id
LEFT JOIN stores s ON ss.store_id = s.id
WHERE p.id = 5070
ORDER BY ss.store_id;

-- 2. Bu mahsulot bilan savdolarni ko'rish
SELECT 
    s.id,
    s.created_at,
    s.payment_status,
    si.quantity,
    si.source_type,
    si.source_id,
    si.notes
FROM sales s
JOIN sale_items si ON s.id = si.sale_id
WHERE si.product_id = 5070
ORDER BY s.created_at DESC
LIMIT 10;

-- 3. Stock history (agar bor bo'lsa)
SELECT * FROM stock_changes 
WHERE product_id = 5070 
ORDER BY created_at DESC 
LIMIT 20;
