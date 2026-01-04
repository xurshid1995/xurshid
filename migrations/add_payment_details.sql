-- To'lov turlarini batafsil saqlash uchun ustunlar qo'shish
ALTER TABLE sales ADD COLUMN IF NOT EXISTS cash_amount DECIMAL(12, 2) DEFAULT 0;
ALTER TABLE sales ADD COLUMN IF NOT EXISTS click_amount DECIMAL(12, 2) DEFAULT 0;
ALTER TABLE sales ADD COLUMN IF NOT EXISTS terminal_amount DECIMAL(12, 2) DEFAULT 0;
ALTER TABLE sales ADD COLUMN IF NOT EXISTS debt_amount DECIMAL(12, 2) DEFAULT 0;

-- Mavjud savdolar uchun eski ma'lumotlarni yangilash
-- Agar payment_method 'cash' bo'lsa, barcha summa cash_amount ga o'tkaziladi
UPDATE sales SET cash_amount = total_amount WHERE payment_method = 'cash' AND cash_amount = 0;
UPDATE sales SET click_amount = total_amount WHERE payment_method = 'click' AND click_amount = 0;
UPDATE sales SET terminal_amount = total_amount WHERE payment_method = 'terminal' AND terminal_amount = 0;
UPDATE sales SET debt_amount = total_amount WHERE payment_method = 'debt' AND debt_amount = 0;
