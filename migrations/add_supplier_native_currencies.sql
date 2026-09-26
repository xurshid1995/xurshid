BEGIN;
SET LOCAL lock_timeout = '10s';
SET LOCAL statement_timeout = '5min';

ALTER TABLE supplier_purchase_batches
    ADD COLUMN IF NOT EXISTS currency_rate NUMERIC(18,4),
    ADD COLUMN IF NOT EXISTS native_total_usd NUMERIC(24,10),
    ADD COLUMN IF NOT EXISTS native_total_uzs NUMERIC(24,2),
    ADD COLUMN IF NOT EXISTS native_debt_usd NUMERIC(24,10),
    ADD COLUMN IF NOT EXISTS native_debt_uzs NUMERIC(24,2),
    ADD COLUMN IF NOT EXISTS native_initial_debts JSON,
    ADD COLUMN IF NOT EXISTS native_payments JSON;
ALTER TABLE supplier_purchases
    ADD COLUMN IF NOT EXISTS cost_currency VARCHAR(3) NOT NULL DEFAULT 'USD',
    ADD COLUMN IF NOT EXISTS native_cost_price NUMERIC(24,10);
ALTER TABLE supplier_payments ADD COLUMN IF NOT EXISTS native_allocation JSON;
ALTER TABLE suppliers ALTER COLUMN balance_usd TYPE NUMERIC(24,10);
ALTER TABLE supplier_purchase_batches
    ALTER COLUMN total_amount TYPE NUMERIC(24,10),
    ALTER COLUMN paid_amount TYPE NUMERIC(24,10),
    ALTER COLUMN debt_amount TYPE NUMERIC(24,10),
    ALTER COLUMN cash_usd TYPE NUMERIC(24,10),
    ALTER COLUMN click_usd TYPE NUMERIC(24,10),
    ALTER COLUMN terminal_usd TYPE NUMERIC(24,10),
    ALTER COLUMN initial_paid_amount TYPE NUMERIC(24,10);
ALTER TABLE supplier_payments
    ALTER COLUMN amount_usd TYPE NUMERIC(24,10),
    ALTER COLUMN cash_usd TYPE NUMERIC(24,10),
    ALTER COLUMN click_usd TYPE NUMERIC(24,10),
    ALTER COLUMN terminal_usd TYPE NUMERIC(24,10);
COMMIT;