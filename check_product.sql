-- Mahsulotlarning tan narxini tekshirish
SELECT 
    p.id,
    p.name,
    p.cost_price,
    p.sell_price,
    (p.sell_price - p.cost_price) as profit_per_unit,
    CASE 
        WHEN p.cost_price = 0 THEN '❌ Tan narx 0'
        WHEN p.cost_price IS NULL THEN '❌ Tan narx NULL'
        ELSE '✅ To''g''ri'
    END as status
FROM products p
ORDER BY 
    CASE 
        WHEN p.cost_price = 0 OR p.cost_price IS NULL THEN 0
        ELSE 1
    END,
    p.id DESC
LIMIT 20;

-- Tan narxi 0 yoki NULL bo'lgan mahsulotlar soni
SELECT 
    COUNT(*) as total_products,
    COUNT(CASE WHEN cost_price = 0 OR cost_price IS NULL THEN 1 END) as zero_or_null_cost,
    COUNT(CASE WHEN cost_price > 0 THEN 1 END) as valid_cost
FROM products;
