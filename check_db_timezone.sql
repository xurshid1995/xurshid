-- Database timezone tekshirish
SELECT 
    'Server timezone' as info, 
    NOW() as current_time,
    CURRENT_TIMESTAMP as timestamp;

-- Timezone setting
SHOW timezone;

-- Eng oxirgi sale'ning vaqti
SELECT id, sale_date, created_at 
FROM sales 
ORDER BY id DESC 
LIMIT 3;
