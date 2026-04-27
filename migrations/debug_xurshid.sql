SELECT s.id, s.debt_usd, s.debt_amount, s.payment_status 
FROM sales s 
JOIN customers c ON s.customer_id=c.id 
WHERE c.name ILIKE '%xurshid%' 
ORDER BY s.id DESC LIMIT 10;
