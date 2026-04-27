-- Floating point xatosi natijasida paydo bo'lgan mikro-qarzlarni (0.001 dan kichik) nolga tuzatish
-- Bu qarzlar to'liq to'langan lekin matematik xato tufayli kichik qoldiq qolgan

UPDATE sales
SET debt_usd = 0,
    debt_amount = 0,
    payment_status = 'paid'
WHERE debt_usd > 0
  AND debt_usd < 0.001
  AND payment_status IN ('partial', 'debt');

-- Necha qator o'zgarganini ko'rsatish
SELECT COUNT(*) AS fixed_rows
FROM sales
WHERE debt_usd = 0
  AND payment_status = 'paid'
  AND updated_at > NOW() - INTERVAL '1 minute';
