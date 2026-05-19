-- Qisman qabul qilish uchun pending_transfers ga yangi ustunlar
-- received_items: JSON [{product_id, received_qty}, ...]
-- received_at: Sotuvchi tasdiqlagan vaqt (duplicate oldini olish)
-- received_note: Ixtiyoriy izoh

ALTER TABLE pending_transfers
    ADD COLUMN IF NOT EXISTS received_items JSON,
    ADD COLUMN IF NOT EXISTS received_at TIMESTAMP,
    ADD COLUMN IF NOT EXISTS received_note TEXT;
