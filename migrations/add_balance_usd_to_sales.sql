-- Sales jadvaliga balance_usd ustunini qo'shish
-- Mijoz balansidan foydalanilgan summani alohida saqlash uchun
ALTER TABLE sales ADD COLUMN IF NOT EXISTS balance_usd DECIMAL(15,10) DEFAULT 0;

-- Mavjud ma'lumotlar uchun 0 ni default qilib o'rnatish
UPDATE sales SET balance_usd = 0 WHERE balance_usd IS NULL;
