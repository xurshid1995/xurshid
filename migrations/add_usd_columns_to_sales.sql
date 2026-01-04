-- Sales jadvaliga USD ustunlarini qo'shish

-- USD ustunlarini qo'shish
ALTER TABLE sales ADD COLUMN IF NOT EXISTS debt_usd NUMERIC(12, 2) DEFAULT 0;
ALTER TABLE sales ADD COLUMN IF NOT EXISTS cash_usd NUMERIC(12, 2) DEFAULT 0;
ALTER TABLE sales ADD COLUMN IF NOT EXISTS click_usd NUMERIC(12, 2) DEFAULT 0;
ALTER TABLE sales ADD COLUMN IF NOT EXISTS terminal_usd NUMERIC(12, 2) DEFAULT 0;

-- Mavjud ma'lumotlarni konvertatsiya qilish (currency_rate yordamida)
UPDATE sales 
SET 
    debt_usd = CASE 
        WHEN currency_rate > 0 THEN ROUND(debt_amount / currency_rate, 2)
        ELSE 0 
    END,
    cash_usd = CASE 
        WHEN currency_rate > 0 THEN ROUND(cash_amount / currency_rate, 2)
        ELSE 0 
    END,
    click_usd = CASE 
        WHEN currency_rate > 0 THEN ROUND(click_amount / currency_rate, 2)
        ELSE 0 
    END,
    terminal_usd = CASE 
        WHEN currency_rate > 0 THEN ROUND(terminal_amount / currency_rate, 2)
        ELSE 0 
    END
WHERE debt_usd IS NULL OR cash_usd IS NULL OR click_usd IS NULL OR terminal_usd IS NULL;

-- Index qo'shish tezlikni oshirish uchun
CREATE INDEX IF NOT EXISTS idx_sales_debt_usd ON sales(debt_usd) WHERE debt_usd > 0;
CREATE INDEX IF NOT EXISTS idx_sales_customer_debt ON sales(customer_id, debt_usd) WHERE debt_usd > 0;

-- Ma'lumotlarni tekshirish
SELECT 
    COUNT(*) as total_sales,
    COUNT(CASE WHEN debt_usd > 0 THEN 1 END) as sales_with_debt,
    SUM(debt_usd) as total_debt_usd,
    SUM(debt_amount) as total_debt_uzs
FROM sales;
