#!/bin/bash

export PGPASSWORD='bwjtaUueHturzUv2TuNf'

echo "=== Current idle connections ==="
psql -h localhost -U xurshid_user -d xurshid_db << 'EOF'
SELECT 
    pid,
    usename,
    state,
    EXTRACT(EPOCH FROM (NOW() - state_change)) as idle_seconds
FROM pg_stat_activity
WHERE datname = 'xurshid_db' 
  AND state = 'idle' 
  AND state_change < NOW() - INTERVAL '5 minutes'
  AND pid != pg_backend_pid();
EOF

echo ""
echo "Note: Eski idle connections avtomatik ravishda pool_recycle orqali yangilanadi (har 9 minutda)"
