-- Qarz to'lash muddati ustunini qo'shish
-- 2026-03-09

ALTER TABLE sales ADD COLUMN IF NOT EXISTS payment_due_date DATE;

-- Index qo'shish (muddat bo'yicha tez qidirish uchun)
CREATE INDEX IF NOT EXISTS idx_sales_payment_due_date ON sales(payment_due_date) WHERE payment_due_date IS NOT NULL AND debt_usd > 0;

COMMENT ON COLUMN sales.payment_due_date IS 'Qarz to''lash muddati sanasi';
