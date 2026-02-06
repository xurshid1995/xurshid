#!/bin/bash
# Test Stock Check Active Sessions API
# This script tests if the active sessions are loading correctly

echo "🧪 Testing Stock Check Active Sessions Fix"
echo "=========================================="
echo ""

# Check if there are active sessions in database
echo "1. Checking database for active sessions..."
ACTIVE_COUNT=$(sudo -u postgres psql -d xurshid_db -tAc "SELECT COUNT(*) FROM stock_check_sessions WHERE status = 'active';")
echo "   Active sessions in DB: $ACTIVE_COUNT"

if [ "$ACTIVE_COUNT" -gt 0 ]; then
    echo "   ✓ Found $ACTIVE_COUNT active session(s)"
    echo ""
    echo "   Sessions details:"
    sudo -u postgres psql -d xurshid_db -c "SELECT id, location_name, location_type, user_id, started_at FROM stock_check_sessions WHERE status = 'active' ORDER BY started_at DESC;"
else
    echo "   ℹ No active sessions found. Create one to test."
fi

echo ""
echo "2. Checking Flask application status..."
if systemctl is-active --quiet xurshid; then
    echo "   ✓ Flask app is running"
else
    echo "   ✗ Flask app is NOT running"
    exit 1
fi

echo ""
echo "3. Checking API endpoint (without auth - will return login error, but proves endpoint works)..."
RESPONSE=$(curl -s http://localhost:5000/api/check_stock/active_sessions)
if echo "$RESPONSE" | grep -q "error"; then
    echo "   ✓ API endpoint responding (requires authentication)"
else
    echo "   Response: $RESPONSE"
fi

echo ""
echo "4. Checking Flask error logs for stock_check issues..."
ERRORS=$(sudo tail -20 /var/www/xurshid/logs/error.log 2>/dev/null | grep -i "stock_check\|NameError\|AttributeError" || echo "")
if [ -z "$ERRORS" ]; then
    echo "   ✓ No errors found in recent logs"
else
    echo "   ⚠ Found potential errors:"
    echo "$ERRORS"
fi

echo ""
echo "5. Verifying Python syntax..."
cd /var/www/xurshid
python3 -m py_compile app.py && echo "   ✓ Python syntax is correct"

echo ""
echo "=========================================="
echo "✅ TEST COMPLETE"
echo ""
echo "TO TEST MANUALLY:"
echo "1. Login to: http://sergeli0606.uz/login"
echo "2. Go to: http://sergeli0606.uz/check_stock"
echo "3. Active sessions should now be visible"
echo ""
echo "If sessions still don't appear:"
echo "  - Check browser console (F12) for JavaScript errors"
echo "  - Verify user has permission to view locations"
echo "  - Check if user's allowed_locations includes the session locations"
