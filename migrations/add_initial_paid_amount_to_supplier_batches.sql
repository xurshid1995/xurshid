-- SupplierPurchaseBatch.initial_paid_amount: guruh qabul qilingan paytdagi (o'zgarmas) to'lov ulushi.
-- Timeline'dagi tarixiy qarz hisobi uchun kerak - paid_amount keyingi qarz to'lovlari bilan
-- yangilanib turadi, shuning uchun tarixiy delta sifatida ishlatib bo'lmaydi.
ALTER TABLE supplier_purchase_batches
    ADD COLUMN IF NOT EXISTS initial_paid_amount NUMERIC(15, 2) NOT NULL DEFAULT 0;

-- Mavjud batchlar uchun orqaga qarab tiklash:
-- initial_paid_amount = joriy paid_amount - shu batchga tegishli qarz to'lovlari yig'indisi
UPDATE supplier_purchase_batches b
SET initial_paid_amount = GREATEST(
    0,
    b.paid_amount - COALESCE((
        SELECT SUM(sp.amount_usd) FROM supplier_payments sp WHERE sp.batch_id = b.id
    ), 0)
);
