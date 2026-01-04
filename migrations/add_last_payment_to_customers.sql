-- Add last payment tracking columns to customers table
ALTER TABLE customers 
ADD COLUMN IF NOT EXISTS last_debt_payment_usd NUMERIC(10, 2) DEFAULT 0,
ADD COLUMN IF NOT EXISTS last_debt_payment_date TIMESTAMP;

-- Add index for faster queries
CREATE INDEX IF NOT EXISTS idx_customers_last_payment_date ON customers(last_debt_payment_date);

-- Update comment
COMMENT ON COLUMN customers.last_debt_payment_usd IS 'Oxirgi qarz to''lovi miqdori (USD)';
COMMENT ON COLUMN customers.last_debt_payment_date IS 'Oxirgi qarz to''lovi sanasi';
