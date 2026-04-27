-- Mikro qarzlarni tuzatish (floating point qoldiqlari < 0.001 USD)
UPDATE sales
SET debt_usd = 0,
    debt_amount = 0,
    payment_status = 'paid'
WHERE debt_usd > 0
  AND debt_usd < 0.001;

SELECT id, debt_usd, debt_amount, payment_status FROM sales WHERE customer_id=15;
