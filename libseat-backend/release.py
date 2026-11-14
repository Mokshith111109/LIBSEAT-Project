"""Run every minute from cron:   * * * * * cd /path/libseat-backend && python release.py
Cancels bookings not checked in within 20 minutes, ends finished ones, and deletes data older than 30 days."""
import os
from datetime import timedelta
os.environ.setdefault("LIBSEAT_NO_AUTOSTART", "1")
import app as A
import db as dbm

RETAIN_DAYS = int(os.environ.get("RETAIN_DAYS", "30"))
A.dbm.configure(os.environ.get("DATABASE_URL", "sqlite:///libseat.db"))
d = dbm.connect()
try:
    n = A.release_unclaimed(d)
    gone = A.purge_old(d)
    print(f"released={n} purged_old_bookings={gone}")
finally:
    d.close()
