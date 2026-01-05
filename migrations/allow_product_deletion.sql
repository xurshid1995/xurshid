-- Product o'chirish imkoniyatini berish
-- Product o'chirilganda sale_items/transfer_items'dagi product_id NULL bo'ladi
-- Lekin notes'da mahsulot nomi saqlanadi

-- 1. sale_items jadvalidagi product_id ni NULL qilish ruxsat berish
ALTER TABLE sale_items ALTER COLUMN product_id DROP NOT NULL;

-- 2. Eski constraint'ni o'chirish va yangi qo'shish (ON DELETE SET NULL)
ALTER TABLE sale_items 
DROP CONSTRAINT IF EXISTS sale_items_product_id_fkey;

ALTER TABLE sale_items 
ADD CONSTRAINT sale_items_product_id_fkey 
FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE SET NULL;

-- 3. transfer_items jadvalidagi product_id ni NULL qilish ruxsat berish (agar mavjud bo'lsa)
DO $$ 
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.tables 
        WHERE table_name = 'transfer_items'
    ) THEN
        ALTER TABLE transfer_items ALTER COLUMN product_id DROP NOT NULL;
        
        ALTER TABLE transfer_items 
        DROP CONSTRAINT IF EXISTS transfer_items_product_id_fkey;
        
        ALTER TABLE transfer_items 
        ADD CONSTRAINT transfer_items_product_id_fkey 
        FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE SET NULL;
    END IF;
END $$;

-- 4. Tekshirish
SELECT 
    constraint_name, 
    table_name,
    delete_rule
FROM information_schema.referential_constraints rc
JOIN information_schema.table_constraints tc 
    ON rc.constraint_name = tc.constraint_name
WHERE tc.table_name IN ('sale_items', 'transfer_items')
    AND constraint_type = 'FOREIGN KEY';
