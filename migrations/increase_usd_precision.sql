-- USD precision'ni 2 dan 4 decimal belgiga oshirish
-- Bu yaxlitlash xatoliklarini kamaytiradi

-- SaleItem jadvalidagi USD maydonlar
ALTER TABLE sale_items
    ALTER COLUMN unit_price TYPE DECIMAL(10, 4),
    ALTER COLUMN total_price TYPE DECIMAL(12, 4),
    ALTER COLUMN cost_price TYPE DECIMAL(10, 4),
    ALTER COLUMN profit TYPE DECIMAL(12, 4);

-- Sales jadvalidagi USD maydonlar
ALTER TABLE sales
    ALTER COLUMN total_amount TYPE DECIMAL(12, 4),
    ALTER COLUMN total_cost TYPE DECIMAL(12, 4),
    ALTER COLUMN total_profit TYPE DECIMAL(12, 4),
    ALTER COLUMN cash_usd TYPE DECIMAL(12, 4),
    ALTER COLUMN click_usd TYPE DECIMAL(12, 4),
    ALTER COLUMN terminal_usd TYPE DECIMAL(12, 4),
    ALTER COLUMN debt_usd TYPE DECIMAL(12, 4);

-- DebtPayment jadvalidagi USD maydonlar
ALTER TABLE debt_payments
    ALTER COLUMN cash_usd TYPE DECIMAL(12, 4),
    ALTER COLUMN click_usd TYPE DECIMAL(12, 4),
    ALTER COLUMN terminal_usd TYPE DECIMAL(12, 4),
    ALTER COLUMN total_usd TYPE DECIMAL(12, 4);

-- Products jadvalidagi narx maydonlar
ALTER TABLE products
    ALTER COLUMN sell_price TYPE DECIMAL(10, 4),
    ALTER COLUMN cost_price TYPE DECIMAL(10, 4),
    ALTER COLUMN last_batch_cost TYPE DECIMAL(10, 4);

-- Transfer jadvalidagi cost maydon(agar mavjud bo'lsa)
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns 
               WHERE table_name = 'transfers' AND column_name = 'cost') THEN
        ALTER TABLE transfers ALTER COLUMN cost TYPE DECIMAL(10, 4);
    END IF;
END $$;

COMMENT ON TABLE sale_items IS 'USD precision 4 decimal - yaxlitlash xatoligi kamaytirildi';
COMMENT ON TABLE sales IS 'USD precision 4 decimal - 120,000 UZS = 9.7561 USD (9.76 emas)';
