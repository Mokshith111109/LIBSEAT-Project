-- PostgreSQL schema (the app also creates it automatically on start)

CREATE TABLE IF NOT EXISTS users(reg_no TEXT PRIMARY KEY, created_at TEXT, last_login TEXT);
CREATE TABLE IF NOT EXISTS admins(username TEXT PRIMARY KEY, pw_hash TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS seats(id TEXT PRIMARY KEY, floor TEXT NOT NULL, sort_no INTEGER NOT NULL,
  x INTEGER NOT NULL, y INTEGER NOT NULL, has_power INTEGER NOT NULL DEFAULT 0, zone TEXT, blocked INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS layout_tables(id SERIAL PRIMARY KEY, floor TEXT NOT NULL,
  x INTEGER NOT NULL, y INTEGER NOT NULL, w INTEGER NOT NULL, h INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS layouts(floor TEXT PRIMARY KEY, image TEXT, decor INTEGER NOT NULL DEFAULT 0, data TEXT);
CREATE TABLE IF NOT EXISTS bookings(id SERIAL PRIMARY KEY, reg_no TEXT NOT NULL, seat_id TEXT NOT NULL,
  day TEXT NOT NULL, start_min INTEGER NOT NULL, end_min INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'booked',
  created_at TEXT, checked_in_at TEXT);
CREATE TABLE IF NOT EXISTS booking_slots(booking_id INTEGER NOT NULL REFERENCES bookings(id) ON DELETE CASCADE,
  seat_id TEXT NOT NULL, reg_no TEXT NOT NULL, day TEXT NOT NULL, slot INTEGER NOT NULL,
  UNIQUE(seat_id, day, slot), UNIQUE(reg_no, day, slot));
CREATE TABLE IF NOT EXISTS legal_docs(kind TEXT PRIMARY KEY, body TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1, updated_at TEXT);
CREATE INDEX IF NOT EXISTS ix_b_day ON bookings(day, status, start_min);
CREATE INDEX IF NOT EXISTS ix_b_user ON bookings(reg_no, day);
CREATE INDEX IF NOT EXISTS ix_slots_b ON booking_slots(booking_id);
