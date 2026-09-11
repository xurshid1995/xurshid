-- SupplierPurchase larni "bitta qabul qilish operatsiyasi" bo'yicha guruhlash uchun (Sale/SaleItem kabi)
CREATE TABLE IF NOT EXISTS supplier_purchase_batches (
    id SERIAL PRIMARY KEY,
    supplier_id INTEGER REFERENCES suppliers(id) ON DELETE SET NULL,
    added_by VARCHAR(100),
    created_at TIMESTAMP DEFAULT NOW()
);

ALTER TABLE supplier_purchases
    ADD COLUMN IF NOT EXISTS batch_id INTEGER REFERENCES supplier_purchase_batches(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_supplier_purchases_batch_id ON supplier_purchases(batch_id);
CREATE INDEX IF NOT EXISTS idx_supplier_purchase_batches_supplier_id ON supplier_purchase_batches(supplier_id);
