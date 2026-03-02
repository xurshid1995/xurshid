-- Hosting mijozlarga balance ustuni qo'shish
-- Mavjud to'lovlar summasini balance ga o'tkazish

ALTER TABLE hosting_clients
ADD COLUMN IF NOT EXISTS balance DECIMAL(15, 2) NOT NULL DEFAULT 0;

-- Mavjud to'lovlar summasini balance ga yozish
UPDATE hosting_clients hc
SET balance = COALESCE((
    SELECT SUM(hp.amount_uzs)
    FROM hosting_payments hp
    WHERE hp.client_id = hc.id
), 0);
