#!/bin/bash

export PGPASSWORD='bwjtaUueHturzUv2TuNf'

echo "=== Sales Table Columns ==="
psql -h localhost -U xurshid_user -d xurshid_db << 'EOF'
SELECT column_name, data_type 
FROM information_schema.columns 
WHERE table_name = 'sales'
ORDER BY ordinal_position;
EOF

echo ""
echo "=== Products Table Columns ==="
psql -h localhost -U xurshid_user -d xurshid_db << 'EOF'
SELECT column_name, data_type 
FROM information_schema.columns 
WHERE table_name = 'products'
ORDER BY ordinal_position;
EOF

echo ""
echo "=== Sale Items Table Columns ==="
psql -h localhost -U xurshid_user -d xurshid_db << 'EOF'
SELECT column_name, data_type 
FROM information_schema.columns 
WHERE table_name = 'sale_items'
ORDER BY ordinal_position;
EOF
