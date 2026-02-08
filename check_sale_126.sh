#!/bin/bash

export PGPASSWORD='bwjtaUueHturzUv2TuNf'

echo "=== Sale 126 Current State ==="
psql -h localhost -U xurshid_user -d xurshid_db << 'EOF'
SELECT 
    s.id,
    s.created_at,
    s.total_amount,
    s.cash_amount,
    s.cash_usd,
    s.click_amount,
    s.click_usd,
    s.terminal_amount,
    s.terminal_usd,
    s.payment_method,
    s.location_id,
    s.location_type,
    si.id as item_id,
    si.product_id,
    p.name,
    si.quantity,
    si.unit_price,
    si.total_price
FROM sales s
LEFT JOIN sale_items si ON s.id = si.sale_id
LEFT JOIN products p ON si.product_id = p.id
WHERE s.id = 126
ORDER BY si.id;
EOF

echo ""
echo "=== Teyes Q8 Product Info ==="
psql -h localhost -U xurshid_user -d xurshid_db << 'EOF'
SELECT id, name, sell_price
FROM products 
WHERE name LIKE '%Teyes Q8%';
EOF

echo ""
echo "=== Teyes Q8 Stock at Sale Location ==="
psql -h localhost -U xurshid_user -d xurshid_db << 'EOF'
SELECT ss.id, ss.product_id, p.name, ss.quantity, s.name as store_name
FROM store_stocks ss
JOIN products p ON ss.product_id = p.id
JOIN stores s ON ss.store_id = s.id
WHERE p.name LIKE '%Teyes Q8%'
ORDER BY ss.id;
EOF
