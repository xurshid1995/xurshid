#!/bin/bash
sudo -u postgres psql sayt_db << 'EOF'
-- Pending savdolarni ko'rish
\echo '=== PENDING SAVDOLAR ==='
SELECT id, customer_id, payment_status, payment_method, 
       to_char(sale_date, 'YYYY-MM-DD HH24:MI:SS') as sale_date,
       to_char(created_at, 'YYYY-MM-DD HH24:MI:SS') as created_at,
       cash_usd, click_usd, terminal_usd, debt_usd
FROM sales 
WHERE payment_status = 'pending' 
ORDER BY created_at DESC 
LIMIT 5;

\echo ''
\echo '=== OXIRGI 10 TA SAVDO (barcha statuslar) ==='
SELECT id, customer_id, payment_status, 
       to_char(sale_date, 'YYYY-MM-DD HH24:MI:SS') as sale_date,
       to_char(created_at, 'YYYY-MM-DD HH24:MI:SS') as created_at,
       cash_usd + click_usd + terminal_usd as total_paid_usd,
       debt_usd
FROM sales 
ORDER BY id DESC 
LIMIT 10;

\echo ''
\echo '=== PAID STATUSDAGI OXIRGI SAVDOLAR ==='
SELECT id, customer_id, 
       to_char(sale_date, 'YYYY-MM-DD HH24:MI:SS') as sale_date,
       to_char(created_at, 'YYYY-MM-DD HH24:MI:SS') as created_at,
       cash_usd, terminal_usd, debt_usd
FROM sales
WHERE payment_status = 'paid'
ORDER BY id DESC
LIMIT 5;

\echo ''
\echo '=== JAMI SAVDOLAR SONI ==='
SELECT payment_status, COUNT(*) as count
FROM sales
GROUP BY payment_status
ORDER BY count DESC;
EOF
