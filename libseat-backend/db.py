"""Tiny database layer. SQLite for development/tests, PostgreSQL for production.
Write SQL with ? placeholders; they are converted to %s for PostgreSQL."""
import os, sqlite3, threading

URL = "sqlite:///libseat.db"
IS_PG = False
IntegrityError = sqlite3.IntegrityError
_pool = None
_lock = threading.Lock()

SCHEMA_SQLITE = """
CREATE TABLE IF NOT EXISTS users(reg_no TEXT PRIMARY KEY, created_at TEXT, last_login TEXT);
CREATE TABLE IF NOT EXISTS admins(username TEXT PRIMARY KEY, pw_hash TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS seats(id TEXT PRIMARY KEY, floor TEXT NOT NULL, sort_no INTEGER NOT NULL,
  x INTEGER NOT NULL, y INTEGER NOT NULL, has_power INTEGER NOT NULL DEFAULT 0, zone TEXT, blocked INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS layout_tables(id INTEGER PRIMARY KEY AUTOINCREMENT, floor TEXT NOT NULL,
  x INTEGER NOT NULL, y INTEGER NOT NULL, w INTEGER NOT NULL, h INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS layouts(floor TEXT PRIMARY KEY, image TEXT, decor INTEGER NOT NULL DEFAULT 0, data TEXT);
CREATE TABLE IF NOT EXISTS bookings(id INTEGER PRIMARY KEY AUTOINCREMENT, reg_no TEXT NOT NULL, seat_id TEXT NOT NULL,
  day TEXT NOT NULL, start_min INTEGER NOT NULL, end_min INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'booked',
  created_at TEXT, checked_in_at TEXT);
CREATE TABLE IF NOT EXISTS booking_slots(booking_id INTEGER NOT NULL REFERENCES bookings(id) ON DELETE CASCADE,
  seat_id TEXT NOT NULL, reg_no TEXT NOT NULL, day TEXT NOT NULL, slot INTEGER NOT NULL,
  UNIQUE(seat_id, day, slot), UNIQUE(reg_no, day, slot));
CREATE TABLE IF NOT EXISTS legal_docs(kind TEXT PRIMARY KEY, body TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1, updated_at TEXT);
CREATE INDEX IF NOT EXISTS ix_b_day ON bookings(day, status, start_min);
CREATE INDEX IF NOT EXISTS ix_b_user ON bookings(reg_no, day);
CREATE INDEX IF NOT EXISTS ix_slots_b ON booking_slots(booking_id);
"""
TABLES = ("users", "admins", "settings", "seats", "layout_tables", "layouts", "bookings", "booking_slots", "legal_docs")
SCHEMA_PG = SCHEMA_SQLITE.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")


def configure(url):
    global URL, IS_PG, IntegrityError, _pool
    if url.startswith("postgres") and "supabase" in url and "sslmode" not in url:
        url += ("&" if "?" in url else "?") + "sslmode=require"      # Supabase only accepts encrypted connections
    URL, _pool = url, None
    IS_PG = url.startswith("postgres")
    if IS_PG:
        import psycopg2
        IntegrityError = psycopg2.IntegrityError
    else:
        IntegrityError = sqlite3.IntegrityError


class DB:
    def __init__(self, conn):
        self.c = conn
        self.tx = False      # True between begin() and commit()/rollback()

    def _s(self, sql):
        return sql.replace("?", "%s") if IS_PG else sql

    def _cur(self):
        if IS_PG:
            import psycopg2.extras
            return self.c.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        return self.c.cursor()

    def _auto(self, sql=""):
        """PostgreSQL does not save automatically, so save single writes made outside begin()...commit()."""
        if IS_PG and not self.tx and sql.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")):
            self.c.commit()

    def q(self, sql, args=()):
        cur = self._cur()
        cur.execute(self._s(sql), args)
        rows = [dict(r) for r in cur.fetchall()] if cur.description else []
        cur.close()
        self._auto(sql)
        return rows

    def one(self, sql, args=()):
        r = self.q(sql, args)
        return r[0] if r else None

    def x(self, sql, args=()):
        cur = self._cur()
        cur.execute(self._s(sql), args)
        n = cur.rowcount
        cur.close()
        self._auto(sql)
        return n

    def many(self, sql, rows):
        cur = self._cur()
        cur.executemany(self._s(sql), rows)
        cur.close()
        self._auto(sql)

    def begin(self, lock_key=None):
        """Start a write transaction. lock_key serialises one user's bookings."""
        self.tx = True
        if IS_PG:
            if lock_key:
                self.q("SELECT pg_advisory_xact_lock(hashtext(?)) AS l", (lock_key,))
        else:
            self.c.execute("BEGIN IMMEDIATE")

    def commit(self):
        self.c.commit() if IS_PG else self.c.execute("COMMIT")
        self.tx = False

    def rollback(self):
        try:
            self.c.rollback() if IS_PG else self.c.execute("ROLLBACK")
        except Exception:
            pass
        self.tx = False

    def close(self):
        if IS_PG:
            self.rollback()
            _pool.putconn(self.c)
        else:
            self.c.close()


def connect():
    global _pool
    if IS_PG:
        import psycopg2.pool
        with _lock:
            if _pool is None:
                _pool = psycopg2.pool.ThreadedConnectionPool(1, int(os.environ.get("DB_POOL_MAX", "10")), URL, connect_timeout=10)
        return DB(_pool.getconn())
    path = URL.replace("sqlite:///", "", 1)
    c = sqlite3.connect(path, timeout=15, isolation_level=None, check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA foreign_keys=ON")
    c.execute("PRAGMA busy_timeout=15000")
    return DB(c)


def host_label():
    """Where the database is, without the password (for the start-up message)."""
    try:
        return URL.split("@", 1)[1].split("/", 1)[0]
    except IndexError:
        return "unknown host"


def init_schema():
    d = connect()
    try:
        if IS_PG:
            cur = d.c.cursor()
            cur.execute(SCHEMA_PG)
            cur.execute("ALTER TABLE layouts ADD COLUMN IF NOT EXISTS data TEXT")   # older databases
            for t in TABLES:    # Supabase also publishes tables over its own web API; this keeps them private (the server is not affected)
                cur.execute(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")
            d.c.commit()
        else:
            d.c.executescript(SCHEMA_SQLITE)
            cols = [r[1] for r in d.c.execute("PRAGMA table_info(layouts)").fetchall()]
            if "data" not in cols:                                                    # older databases
                d.c.execute("ALTER TABLE layouts ADD COLUMN data TEXT")
    finally:
        d.close()
