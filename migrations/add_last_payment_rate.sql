-- Customers jadvaliga oxirgi to'lov paytdagi kurs ustunini qo'shish
ALTER TABLE customers 
ADD COLUMN IF NOT EXISTS last_debt_payment_rate NUMERIC(10, 2) DEFAULT 0;

-- Mavjud ma'lumotlar uchun default qiymat o'rnatish (13000)
UPDATE customers 
SET last_debt_payment_rate = 13000 
WHERE last_debt_payment_rate IS NULL OR last_debt_payment_rate = 0;

COMMENT ON COLUMN customers.last_debt_payment_rate IS 'Oxirgi to''lov qilingan paytdagi valyuta kursi';
