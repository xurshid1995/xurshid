-- Customers jadvaliga balance ustuni qo'shish
ALTER TABLE customers ADD COLUMN IF NOT EXISTS balance DECIMAL(15,4) NOT NULL DEFAULT 0;

-- Izoh
COMMENT ON COLUMN customers.balance IS 'Mijoz balansi - ortiqcha to''lov yoki oldindan to''lov';
