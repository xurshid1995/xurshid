from app import app, db
from sqlalchemy import text

with app.app_context():
    # Sale 858 location tekshirish
    r = db.session.execute(text(
        "SELECT id, location_id, location_type, payment_status, debt_usd FROM sales WHERE id=858"
    ))
    row = r.fetchone()
    print(f"Sale 858: location_id={row.location_id}  type={row.location_type}  status={row.payment_status}  debt={row.debt_usd}")

    # Non-admin userlar va ularning allowed_locations
    print("\n=== ALL USERS ===")
    r2 = db.session.execute(text(
        "SELECT id, username, role, allowed_locations FROM users ORDER BY role"
    ))
    for u in r2:
        print(f"  id={u.id}  username={u.username}  role={u.role}  allowed={u.allowed_locations}")
