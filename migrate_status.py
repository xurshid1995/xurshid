from app import app, db
from sqlalchemy import text, inspect

with app.app_context():
    db.create_all()
    inspector = inspect(db.engine)
    tables = inspector.get_table_names()
    has_pt = 'pending_transfers' in tables
    print('pending_transfers exists:', has_pt)

    if has_pt:
        cols = [c['name'] for c in inspector.get_columns('pending_transfers')]
        print('Existing columns:', cols)

        new_cols = [
            ('status', "ALTER TABLE pending_transfers ADD COLUMN IF NOT EXISTS status VARCHAR(20) DEFAULT 'draft'"),
            ('sent_at', 'ALTER TABLE pending_transfers ADD COLUMN IF NOT EXISTS sent_at TIMESTAMP'),
            ('dispatched_at', 'ALTER TABLE pending_transfers ADD COLUMN IF NOT EXISTS dispatched_at TIMESTAMP'),
            ('dispatched_by_id', 'ALTER TABLE pending_transfers ADD COLUMN IF NOT EXISTS dispatched_by_id INTEGER REFERENCES users(id) ON DELETE SET NULL'),
            ('receiver_confirmed_at', 'ALTER TABLE pending_transfers ADD COLUMN IF NOT EXISTS receiver_confirmed_at TIMESTAMP'),
        ]

        for col_name, stmt in new_cols:
            if col_name not in cols:
                db.session.execute(text(stmt))
                print('Added column:', col_name)
            else:
                print('Already exists:', col_name)

        db.session.execute(text("UPDATE pending_transfers SET status = 'draft' WHERE status IS NULL"))
        db.session.commit()
        print('Migration done!')
    else:
        print('ERROR: pending_transfers table was not created!')
