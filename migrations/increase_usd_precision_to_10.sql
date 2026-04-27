-- USD qiymatlar uchun precision ni 10 ga ko'tarish
-- Maqsad: 9.9173553719 kabi aniq USD qiymatlarni saqlash
-- (oldingi scale=5 da 9.91736 ga yaxlitlanardi va USD*rate = 120,000.056 xatosi berardi)

-- sale_items jadval
ALTER TABLE sale_items
    ALTER COLUMN unit_price  TYPE DECIMAL(15, 10),
    ALTER COLUMN total_price TYPE DECIMAL(18, 10),
    ALTER COLUMN cost_price  TYPE DECIMAL(15, 10),
    ALTER COLUMN profit      TYPE DECIMAL(18, 10);

-- sales jadval
ALTER TABLE sales
    ALTER COLUMN total_amount  TYPE DECIMAL(15, 10),
    ALTER COLUMN total_cost    TYPE DECIMAL(15, 10),
    ALTER COLUMN total_profit  TYPE DECIMAL(15, 10),
    ALTER COLUMN cash_usd      TYPE DECIMAL(15, 10),
    ALTER COLUMN click_usd     TYPE DECIMAL(15, 10),
    ALTER COLUMN terminal_usd  TYPE DECIMAL(15, 10),
    ALTER COLUMN debt_usd      TYPE DECIMAL(15, 10);
