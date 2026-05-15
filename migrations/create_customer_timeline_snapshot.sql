-- Mijoz amallar tarixi snapshot jadvali
-- Har bir savdo/to'lov yaratilganda bir marta yoziladi, hech qachon o'zgartirilmaydi
-- Faqat timeline ko'rsatish uchun ishlatiladi, hisob-kitobga ta'sir qilmaydi

CREATE TABLE IF NOT EXISTS customer_timeline_snapshot (
    id              SERIAL PRIMARY KEY,
    customer_id     INTEGER NOT NULL,
    event_type      VARCHAR(20) NOT NULL,   -- 'sale', 'payment', 'return'
    event_id        INTEGER NOT NULL,        -- sale.id yoki debt_payment.id
    event_date      TIMESTAMP NOT NULL,      -- amal sodir bo'lgan vaqt
    snapshot_data   JSONB NOT NULL,          -- o'sha paytdagi ma'lumotlar (o'zgarmaydi)
    debt_before     DECIMAL(12,2) DEFAULT 0, -- amaldan OLDIN mijoz qarz
    debt_after      DECIMAL(12,2) DEFAULT 0, -- amaldan KEYIN mijoz qarz
    balance_before  DECIMAL(12,2) DEFAULT 0, -- amaldan OLDIN mijoz balans
    balance_after   DECIMAL(12,2) DEFAULT 0, -- amaldan KEYIN mijoz balans
    created_at      TIMESTAMP DEFAULT NOW()
);

-- Index: bir mijozning barcha amallarini tez topish uchun
CREATE INDEX IF NOT EXISTS idx_cts_customer_id
    ON customer_timeline_snapshot (customer_id, event_date DESC);

-- Index: event_id bo'yicha duplikat oldini olish uchun
CREATE UNIQUE INDEX IF NOT EXISTS idx_cts_event_unique
    ON customer_timeline_snapshot (event_type, event_id);
