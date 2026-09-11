-- Yetkazib beruvchidan mahsulot qabul qilinganda boshlang'ich to'lovning
-- naqd/click/terminal taqsimotini saqlash uchun ustunlar
ALTER TABLE supplier_purchases
    ADD COLUMN IF NOT EXISTS cash_usd DECIMAL(15, 2) NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS click_usd DECIMAL(15, 2) NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS terminal_usd DECIMAL(15, 2) NOT NULL DEFAULT 0;

-- Mavjud yozuvlar uchun paid_amount ni naqd sifatida belgilash (eski ma'lumotlar uchun ma'qul taxmin)
UPDATE supplier_purchases
SET cash_usd = paid_amount
WHERE paid_amount > 0 AND cash_usd = 0 AND click_usd = 0 AND terminal_usd = 0;
