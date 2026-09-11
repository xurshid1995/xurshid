-- Yetkazib beruvchi qarzini mahsulot(qator) darajasidan GURUH (bitta "qabul qilish"
-- operatsiyasi - SupplierPurchaseBatch) darajasiga ko'chirish - mijozdagi Sale kabi.

-- 1) Batch jadvaliga moliyaviy ustunlarni qo'shish
ALTER TABLE supplier_purchase_batches
    ADD COLUMN IF NOT EXISTS total_amount DECIMAL(15, 2) NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS payment_type VARCHAR(20) NOT NULL DEFAULT 'cash',
    ADD COLUMN IF NOT EXISTS paid_amount DECIMAL(15, 2) NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS debt_amount DECIMAL(15, 2) NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS cash_usd DECIMAL(15, 2) NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS click_usd DECIMAL(15, 2) NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS terminal_usd DECIMAL(15, 2) NOT NULL DEFAULT 0;

-- 2) Mavjud batch'larni ularning mahsulot qatorlaridan yig'ib to'ldirish (backfill)
UPDATE supplier_purchase_batches b
SET
    total_amount = agg.total_amount,
    paid_amount  = agg.paid_amount,
    debt_amount  = agg.debt_amount,
    cash_usd     = agg.cash_usd,
    click_usd    = agg.click_usd,
    terminal_usd = agg.terminal_usd,
    payment_type = CASE
        WHEN agg.debt_amount <= 0 THEN 'cash'
        WHEN agg.paid_amount <= 0 THEN 'debt'
        ELSE 'partial'
    END
FROM (
    SELECT
        batch_id,
        SUM(total_amount)  AS total_amount,
        SUM(paid_amount)   AS paid_amount,
        SUM(debt_amount)   AS debt_amount,
        SUM(cash_usd)      AS cash_usd,
        SUM(click_usd)     AS click_usd,
        SUM(terminal_usd)  AS terminal_usd
    FROM supplier_purchases
    WHERE batch_id IS NOT NULL
    GROUP BY batch_id
) agg
WHERE b.id = agg.batch_id;

-- 3) To'lovlar jadvaliga batch_id qo'shish (FIFO bundan buyon guruh darajasida ishlaydi)
ALTER TABLE supplier_payments
    ADD COLUMN IF NOT EXISTS batch_id INTEGER REFERENCES supplier_purchase_batches(id) ON DELETE SET NULL;

-- 4) Mavjud to'lovlarni ularning purchase_id orqali tegishli batch_id bilan bog'lash
UPDATE supplier_payments sp
SET batch_id = p.batch_id
FROM supplier_purchases p
WHERE sp.purchase_id = p.id AND sp.batch_id IS NULL AND p.batch_id IS NOT NULL;
