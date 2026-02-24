import sys, json
sys.path.insert(0, "/var/www/xurshid")
from app import app, db, User, PendingTransfer

with app.app_context():
    users = User.query.filter(User.role != "admin").all()
    for u in users:
        print(f"ID={u.id} username={u.username} role={u.role}")
        print(f"  transfer_locations={json.dumps(u.transfer_locations)}")
        print(f"  allowed_locations={json.dumps(u.allowed_locations)}")
    print("---PENDING---")
    pts = PendingTransfer.query.all()
    for p in pts:
        print(f"PT id={p.id} user_id={p.user_id} from={p.from_location_type}_{p.from_location_id} to={p.to_location_type}_{p.to_location_id}")
        # Simulate check
        u2 = User.query.get(p.user_id)
        if u2:
            tl = u2.transfer_locations or []
            al = u2.allowed_locations or []
            all_locs = tl + al
            print(f"  user={u2.username} all_locs={json.dumps(all_locs)}")
            for loc in all_locs:
                if isinstance(loc, dict):
                    lid = loc.get('id')
                    ltype = loc.get('type')
                    print(f"    loc id={lid!r}(type={type(lid).__name__}) type={ltype!r} | from={p.from_location_id}(int) from_type={p.from_location_type!r}")
                    try:
                        lid_int = int(lid)
                    except:
                        lid_int = None
                    print(f"    match_from: lid_int={lid_int}=={p.from_location_id} and ltype={ltype!r}=={p.from_location_type!r} -> {lid_int==p.from_location_id and ltype==p.from_location_type}")
                    print(f"    match_to:   lid_int={lid_int}=={p.to_location_id} and ltype={ltype!r}=={p.to_location_type!r} -> {lid_int==p.to_location_id and ltype==p.to_location_type}")
