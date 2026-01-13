SELECT id, customer_id, payment_status, payment_method,
       to_char(sale_date, 'YYYY-MM-DD HH24:MI:SS') as sale_date,
       to_char(created_at, 'YYYY-MM-DD HH24:MI:SS') as created_at,
       cash_usd, debt_usd
FROM sales 
WHERE id = 120;
