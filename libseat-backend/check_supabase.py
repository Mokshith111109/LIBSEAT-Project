"""Run:  python check_supabase.py
Checks the connection to your Supabase database and lists the LibSeat tables."""
import os, sys
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass
import db

url = os.environ.get("DATABASE_URL", "")
if not url.startswith("postgres"):
    sys.exit("DATABASE_URL is missing. Copy .env.example to .env and paste your Supabase connection string into it.")
db.configure(url)
try:
    d = db.connect()
except Exception as e:
    sys.exit(f"Could not connect:\n{e}\n\nSee 'If something goes wrong' in SUPABASE.md.")
try:
    print("Connected to PostgreSQL at", db.host_label())
    db.init_schema()
    rows = d.q("SELECT relname, relrowsecurity FROM pg_class WHERE relkind='r' AND relname = ANY(?) ORDER BY relname", (list(db.TABLES),))
    for r in rows:
        n = d.one(f"SELECT COUNT(*) AS n FROM {r['relname']}")["n"]
        print(f"  {r['relname']:15} rows: {n:5}   private (row level security): {'yes' if r['relrowsecurity'] else 'NO'}")
    print("All good." if len(rows) == len(db.TABLES) else "Some tables are missing. Start the app once, or run supabase_schema.sql.")
finally:
    d.close()
