#!/bin/bash
# Server Performance Analysis Script
# 5 ta do'kon + 5 ta sklad monitoring
# Server: 164.92.177.172

echo "╔════════════════════════════════════════════════════════════════╗"
echo "║         SERVER PERFORMANCE MONITORING - $(date +%Y-%m-%d)          ║"
echo "╚════════════════════════════════════════════════════════════════╝"
echo ""

# ============================================
# 1. SYSTEM RESOURCES
# ============================================
echo "📊 SYSTEM RESOURCES:"
echo "─────────────────────────────────────────"
echo "RAM Usage:"
free -h | grep -E "Mem|Swap"
echo ""

echo "CPU Info:"
echo "Cores: $(nproc)"
top -bn1 | grep "Cpu(s)" | head -1
echo ""

echo "Disk Usage:"
df -h / | tail -1
echo ""

# ============================================
# 2. RUNNING PROCESSES
# ============================================
echo "🔄 RUNNING PROCESSES:"
echo "─────────────────────────────────────────"
echo "Gunicorn Workers:"
ps aux | grep gunicorn | grep -v grep | wc -l | xargs echo "Workers:"
ps aux | grep gunicorn | grep -v grep | awk '{sum+=$6} END {print "RAM: " sum/1024 " MB"}'
echo ""

echo "PostgreSQL:"
ps aux | grep postgres | grep -v grep | wc -l | xargs echo "Processes:"
ps aux | grep postgres | grep -v grep | awk '{sum+=$6} END {print "RAM: " sum/1024 " MB"}'
echo ""

# ============================================
# 3. DATABASE STATISTICS
# ============================================
echo "💾 DATABASE STATISTICS:"
echo "─────────────────────────────────────────"

sudo -u postgres psql -d xurshid_db -t << 'EOSQL'
\echo 'Locations:'
SELECT 
    'Stores: ' || COUNT(*) FROM stores
UNION ALL
SELECT 
    'Warehouses: ' || COUNT(*) FROM warehouses;

\echo ''
\echo 'Data Counts:'
SELECT 'Products: ' || COUNT(*) FROM products
UNION ALL
SELECT 'Sales: ' || COUNT(*) FROM sales
UNION ALL
SELECT 'Customers: ' || COUNT(*) FROM customers
UNION ALL
SELECT 'Warehouse Stocks: ' || COUNT(*) FROM warehouse_stocks
UNION ALL
SELECT 'Store Stocks: ' || COUNT(*) FROM store_stocks;

\echo ''
\echo 'Database Size:'
SELECT pg_size_pretty(pg_database_size('xurshid_db')) as "Total Size";

\echo ''
\echo 'Top 5 Largest Tables:'
SELECT 
    schemaname || '.' || tablename as table_name,
    pg_size_pretty(pg_total_relation_size(schemaname||'.'||tablename)) as size
FROM pg_tables 
WHERE schemaname = 'public' 
ORDER BY pg_total_relation_size(schemaname||'.'||tablename) DESC 
LIMIT 5;
EOSQL

echo ""

# ============================================
# 4. DATABASE CONNECTIONS
# ============================================
echo "🔌 DATABASE CONNECTIONS:"
echo "─────────────────────────────────────────"

sudo -u postgres psql -d xurshid_db -t << 'EOSQL'
SELECT 
    'Active: ' || COUNT(*) FROM pg_stat_activity WHERE state = 'active'
UNION ALL
SELECT 
    'Idle: ' || COUNT(*) FROM pg_stat_activity WHERE state = 'idle'
UNION ALL
SELECT 
    'Total: ' || COUNT(*) FROM pg_stat_activity;
EOSQL

echo ""

# ============================================
# 5. SLOW QUERIES (if pg_stat_statements enabled)
# ============================================
echo "🐌 PERFORMANCE INSIGHTS:"
echo "─────────────────────────────────────────"

# Check if pg_stat_statements is enabled
if sudo -u postgres psql -d xurshid_db -tAc "SELECT 1 FROM pg_extension WHERE extname='pg_stat_statements'" | grep -q 1; then
    sudo -u postgres psql -d xurshid_db -t << 'EOSQL'
\echo 'Top 5 Slowest Queries (by mean time):'
SELECT 
    LEFT(query, 60) as query,
    calls,
    ROUND(mean_exec_time::numeric, 2) as avg_time_ms
FROM pg_stat_statements 
WHERE query NOT LIKE '%pg_stat_statements%'
ORDER BY mean_exec_time DESC 
LIMIT 5;
EOSQL
else
    echo "pg_stat_statements not enabled. To enable:"
    echo "  1. Add to postgresql.conf: shared_preload_libraries = 'pg_stat_statements'"
    echo "  2. CREATE EXTENSION pg_stat_statements;"
    echo "  3. Restart PostgreSQL"
fi

echo ""

# ============================================
# 6. RECENT ERRORS (from PostgreSQL log)
# ============================================
echo "⚠️  RECENT ERRORS:"
echo "─────────────────────────────────────────"
LOG_FILE="/var/log/postgresql/postgresql-16-main.log"
if [ -f "$LOG_FILE" ]; then
    echo "Last 5 errors:"
    grep -i error "$LOG_FILE" | tail -5
else
    echo "Log file not found at $LOG_FILE"
fi

echo ""

# ============================================
# 7. APPLICATION STATUS
# ============================================
echo "🌐 APPLICATION STATUS:"
echo "─────────────────────────────────────────"
if systemctl is-active --quiet xurshid_app; then
    echo "✅ Flask App: Running"
else
    echo "❌ Flask App: Stopped"
fi

if systemctl is-active --quiet nginx; then
    echo "✅ Nginx: Running"
else
    echo "❌ Nginx: Not running"
fi

if systemctl is-active --quiet postgresql; then
    echo "✅ PostgreSQL: Running"
else
    echo "❌ PostgreSQL: Not running"
fi

echo ""

# ============================================
# 8. CAPACITY ANALYSIS
# ============================================
echo "📈 CAPACITY ANALYSIS:"
echo "─────────────────────────────────────────"

TOTAL_RAM_KB=$(grep MemTotal /proc/meminfo | awk '{print $2}')
USED_RAM_KB=$(grep MemAvailable /proc/meminfo | awk '{print $2}')
RAM_PERCENT=$(echo "scale=1; (1 - $USED_RAM_KB / $TOTAL_RAM_KB) * 100" | bc)

echo "RAM Usage: ${RAM_PERCENT}%"

if (( $(echo "$RAM_PERCENT > 75" | bc -l) )); then
    echo "⚠️  WARNING: RAM usage high (>75%). Consider upgrade to 4GB"
elif (( $(echo "$RAM_PERCENT > 60" | bc -l) )); then
    echo "⚠️  CAUTION: RAM usage moderate (>60%)"
else
    echo "✅ RAM usage healthy (<60%)"
fi

# Database connections
ACTIVE_CONN=$(sudo -u postgres psql -d xurshid_db -tAc "SELECT COUNT(*) FROM pg_stat_activity WHERE state = 'active'")
echo ""
echo "Active DB Connections: $ACTIVE_CONN"
if [ "$ACTIVE_CONN" -gt 20 ]; then
    echo "⚠️  WARNING: High connection count (>20)"
else
    echo "✅ Connection count healthy"
fi

echo ""
echo "╔════════════════════════════════════════════════════════════════╗"
echo "║                    MONITORING COMPLETE                         ║"
echo "╚════════════════════════════════════════════════════════════════╝"
