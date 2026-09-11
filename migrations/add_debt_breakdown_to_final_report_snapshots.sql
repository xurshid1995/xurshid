ALTER TABLE final_report_snapshots
    ADD COLUMN IF NOT EXISTS customer_debt_usd DECIMAL(15, 2) NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS supplier_debt_usd DECIMAL(15, 2) NOT NULL DEFAULT 0;

UPDATE final_report_snapshots
SET customer_debt_usd = debt_usd
WHERE customer_debt_usd = 0 AND debt_usd <> 0;
