-- Savdolar va ularning foydalarini tekshirish
SELECT 
    s.id,
    s.sale_date,
    s.total_amount,
    s.total_cost,
    s.total_profit,
    COUNT(si.id) as items_count,
    SUM(si.quantity) as total_quantity,
    SUM(si.total_price) as sum_total_price,
    SUM(si.profit) as sum_item_profits
FROM sales s
LEFT JOIN sale_items si ON s.id = si.sale_id
WHERE s.payment_status != 'pending'
GROUP BY s.id, s.sale_date, s.total_amount, s.total_cost, s.total_profit
ORDER BY s.id DESC
LIMIT 10;

-- Har bir sale_item'ning foydasi
SELECT 
    si.id,
    si.sale_id,
    p.name as product_name,
    si.quantity,
    si.unit_price,
    si.cost_price,
    si.total_price,
    si.profit,
    (si.unit_price - si.cost_price) * si.quantity as calculated_profit
FROM sale_items si
LEFT JOIN products p ON si.product_id = p.id
ORDER BY si.sale_id DESC, si.id DESC
LIMIT 20;
