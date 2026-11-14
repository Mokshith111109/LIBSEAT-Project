"""LibSeat backend (Flask). Run: python app.py   |   Production: gunicorn -c gunicorn.conf.py app:app"""
import os, re, json, hmac, hashlib, time, threading
from datetime import datetime, timedelta, timezone
from functools import wraps
from zoneinfo import ZoneInfo
import jwt
from flask import Flask, request, jsonify, g
from werkzeug.security import generate_password_hash, check_password_hash
import db as dbm

try:                      # read settings (DATABASE_URL etc.) from the .env file if there is one
    from dotenv import load_dotenv, dotenv_values
    for _k, _v in dotenv_values().items():     # a value typed in the terminal ($env:NAME=...) beats .env, which is confusing
        if _v is not None and _k in os.environ and os.environ[_k] != _v:
            print(f"WARNING: {_k} was set in this terminal, so it is used instead of the value in .env. "
                  f"Close this terminal (or run: Remove-Item Env:{_k}) and start again to use .env.")
    load_dotenv()
except ImportError:
    pass

TZ = ZoneInfo(os.environ.get("TZ_NAME", "Asia/Kolkata"))
GRACE = int(os.environ.get("GRACE_MINUTES", "20"))   # minutes after start to check in
EARLY = 15            # check-in opens this many minutes before the start
STEP = 15             # booking times are in 15-minute steps
MIN_STAY = 30         # minutes
MAX_PER_DAY = 2
ACTIVE = "('booked','checked_in')"
REG_RE = re.compile(r"^RA\d{13}$")
FLOORS = ("G", "F")


def now_local():
    fake = os.environ.get("FAKE_NOW")   # testing only, e.g. FAKE_NOW=2026-10-31T10:00 (ignored in production)
    if fake and os.environ.get("APP_ENV") != "production":
        return datetime.fromisoformat(fake).replace(tzinfo=TZ)
    return datetime.now(TZ)


def err(code, message, status):
    return jsonify(error=code, message=message), status


# ---------- setup ----------
def create_app(database_url=None):
    app = Flask(__name__)
    app.config["JSON_SORT_KEYS"] = False
    url = database_url or os.environ.get("DATABASE_URL", "")
    if not url:
        print("WARNING: DATABASE_URL is not set, so data is stored in the local file libseat.db on this computer. "
              "Set DATABASE_URL to your Supabase connection string (see SUPABASE.md).")
        url = "sqlite:///libseat.db"
    dbm.configure(url)
    print("Database:", "PostgreSQL (" + dbm.host_label() + ")" if dbm.IS_PG else "SQLite file")
    prod = os.environ.get("APP_ENV") == "production"
    secret = os.environ.get("SECRET_KEY")
    if prod and not secret:
        raise RuntimeError("Set SECRET_KEY in production")
    app.config["SECRET"] = secret or "dev-secret-change-me"
    app.config["QR_SECRET"] = (os.environ.get("QR_SECRET") or app.config["SECRET"]).encode()
    dbm.init_schema()
    d = dbm.connect()
    try:
        seed(d)
    finally:
        d.close()
    register(app)
    return app


def default_layout(floor):
    seats, tables = [], []
    X0, RY = [34, 206], [82, 182, 282, 382]
    for t in range(8):
        x, y = X0[t % 2], RY[t // 2]
        tables.append(dict(x=x, y=y, w=120, h=30))
        for k, (dx, dy) in enumerate([(10, -24), (70, -24), (10, 32), (70, 32)]):
            n = t * 4 + k + 1
            zone = "Quiet zone" if t < 2 else "Reading hall" if t < 6 else "Group study"
            seats.append(dict(id=f"{floor}{n}", x=x + dx, y=y + dy, pw=n % 3 == 0, z=zone))
    return seats, tables


def seed(d):
    if not d.one("SELECT value FROM settings WHERE key='open'"):
        d.x("INSERT INTO settings(key,value) VALUES('open','7')")
        d.x("INSERT INTO settings(key,value) VALUES('close','23')")
    if not d.one("SELECT id FROM seats LIMIT 1"):
        for f in FLOORS:
            seats, tables = default_layout(f)
            d.many("INSERT INTO seats(id,floor,sort_no,x,y,has_power,zone) VALUES(?,?,?,?,?,?,?)",
                   [(s["id"], f, int(s["id"][1:]), s["x"], s["y"], int(s["pw"]), s["z"]) for s in seats])
            d.many("INSERT INTO layout_tables(floor,x,y,w,h) VALUES(?,?,?,?,?)",
                   [(f, t["x"], t["y"], t["w"], t["h"]) for t in tables])
            d.x("INSERT INTO layouts(floor,image,decor) VALUES(?,NULL,1)", (f,))
    user, pw = os.environ.get("ADMIN_USER", "admin"), os.environ.get("ADMIN_PASSWORD")
    if pw:      # ADMIN_PASSWORD in .env is the admin password: it is created on first start and updated whenever you change it
        row = d.one("SELECT pw_hash FROM admins WHERE username=?", (user,))
        if not row:
            d.x("INSERT INTO admins(username,pw_hash) VALUES(?,?)", (user, generate_password_hash(pw)))
        elif not check_password_hash(row["pw_hash"], pw):
            d.x("UPDATE admins SET pw_hash=? WHERE username=?", (generate_password_hash(pw), user))


# ---------- helpers ----------
_cache, _cache_lock = {}, threading.Lock()
_buckets, _b_lock = {}, threading.Lock()
_last_release = [0.0]


def cached(key, ttl, fn):
    now = time.time()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and hit[0] > now:
            return hit[1]
    val = fn()
    with _cache_lock:
        if len(_cache) > 5000:
            _cache.clear()
        _cache[key] = (now + ttl, val)
    return val


def clear_cache():
    with _cache_lock:
        _cache.clear()


def limited(rate, burst):
    """Simple per-user token bucket (per process). Use Redis to share it between servers."""
    def deco(fn):
        @wraps(fn)
        def w(*a, **k):
            if os.environ.get("RATE_LIMIT", "1") == "0":
                return fn(*a, **k)
            key = (fn.__name__, getattr(g, "user", None) or request.remote_addr)
            now = time.time()
            with _b_lock:
                tokens, last = _buckets.get(key, (burst, now))
                tokens = min(burst, tokens + (now - last) * rate)
                if tokens < 1:
                    _buckets[key] = (tokens, now)
                    return err("too_many_requests", "Too many requests. Please wait a moment.", 429)
                _buckets[key] = (tokens - 1, now)
                if len(_buckets) > 50000:
                    _buckets.clear()
            return fn(*a, **k)
        return w
    return deco


def make_token(app, sub, role):
    exp = datetime.now(timezone.utc) + timedelta(hours=12)
    return jwt.encode({"sub": sub, "role": role, "exp": exp}, app.config["SECRET"], algorithm="HS256")


def auth(role=None):
    def deco(fn):
        @wraps(fn)
        def w(*a, **k):
            h = request.headers.get("Authorization", "")
            if not h.startswith("Bearer "):
                return err("unauthorized", "Please log in.", 401)
            try:
                p = jwt.decode(h[7:], g.app.config["SECRET"], algorithms=["HS256"])
            except jwt.PyJWTError:
                return err("unauthorized", "Your session expired. Please log in again.", 401)
            if role and p.get("role") != role:
                return err("forbidden", "You are not allowed to do that.", 403)
            g.user, g.role = p["sub"], p["role"]
            return fn(*a, **k)
        return w
    return deco


def clock():
    n = now_local()
    return n.strftime("%Y-%m-%d"), n.hour * 60 + n.minute


def settings(d):
    r = {x["key"]: int(x["value"]) for x in d.q("SELECT key,value FROM settings")}
    return r.get("open", 7), r.get("close", 23)


def seat_token(app, seat):
    return hmac.new(app.config["QR_SECRET"], seat.encode(), hashlib.sha256).hexdigest()[:16]


def release_unclaimed(d):
    """Cancel bookings not checked in within GRACE minutes of the start; end finished ones."""
    day, nm = clock()
    d.begin()
    try:
        n = d.x(f"UPDATE bookings SET status='released' WHERE status='booked' AND (day<? OR (day=? AND start_min+?<=?))",
                (day, day, GRACE, nm))
        d.x(f"UPDATE bookings SET status='ended' WHERE status IN {ACTIVE} AND (day<? OR (day=? AND end_min<=?))",
            (day, day, nm))
        d.x("DELETE FROM booking_slots WHERE booking_id IN (SELECT id FROM bookings WHERE status NOT IN ('booked','checked_in'))")
        d.commit()
    except Exception:
        d.rollback()
        raise
    clear_cache()
    return n


def purge_old(d):
    """Delete bookings older than RETAIN_DAYS (default 30). Returns how many were deleted."""
    cutoff = (now_local() - timedelta(days=int(os.environ.get("RETAIN_DAYS", "30")))).strftime("%Y-%m-%d")
    d.begin()
    try:
        n = d.x("DELETE FROM bookings WHERE day<?", (cutoff,))
        d.commit()
    except Exception:
        d.rollback()
        raise
    return n


def maybe_release(d):
    t = time.time()
    if t - _last_release[0] < 10:
        return
    _last_release[0] = t
    release_unclaimed(d)


def parse_range(data):
    try:
        s, e = int(data.get("start")), int(data.get("end"))
    except (TypeError, ValueError):
        return None
    if s % STEP or e % STEP or s < 0 or e > 1440 or e - s < MIN_STAY:
        return None
    return s, e


def bk_json(b):
    return {k: b[k] for k in ("id", "seat_id", "day", "start_min", "end_min", "status", "checked_in_at")}


# ---------- routes ----------
def register(app):
    @app.before_request
    def _before():
        g.app = app
        if request.method == "OPTIONS":
            return ("", 204)
        g.db = dbm.connect()

    @app.teardown_request
    def _after(exc):
        d = g.pop("db", None)
        if d:
            d.close()

    @app.after_request
    def _headers(resp):
        origin = request.headers.get("Origin", "")
        allowed = [o.strip() for o in os.environ.get("ALLOWED_ORIGINS", "*").split(",")]
        local = os.environ.get("APP_ENV") != "production" and re.match(r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$", origin)
        if origin and ("*" in allowed or origin in allowed or local):   # on your own computer the local site is always allowed
            resp.headers["Access-Control-Allow-Origin"] = origin if "*" not in allowed else "*"
            resp.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type"
            resp.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.errorhandler(404)
    def _nf(e):
        return err("not_found", "That address does not exist.", 404)

    @app.errorhandler(500)
    def _ise(e):
        return err("server_error", "Something went wrong on the server.", 500)

    @app.get("/healthz")
    def healthz():
        g.db.one("SELECT 1 AS ok")
        return jsonify(ok=True)

    @app.post("/api/internal/release")
    def cron_release():
        """For a free scheduler (e.g. cron-job.org) when you cannot run release.py: POST here every minute."""
        key = os.environ.get("CRON_KEY", "")
        if not key or not hmac.compare_digest(request.headers.get("X-Cron-Key", ""), key):
            return err("forbidden", "Not allowed.", 403)
        return jsonify(released=release_unclaimed(g.db), purged=purge_old(g.db))

    @app.post("/api/login")
    @limited(0.5, 10)
    def login():
        data = request.get_json(silent=True) or {}
        reg = str(data.get("reg_no", "")).strip().upper()
        if not REG_RE.match(reg):
            return err("invalid_reg_no", "Not valid. Enter RA followed by 13 digits (15 characters in total).", 400)
        if os.environ.get("DEV_LOGIN") != "1":
            return err("sso_required", "Student login must go through the college sign-in.", 501)
        ts = now_local().isoformat()
        g.db.x("INSERT INTO users(reg_no,created_at,last_login) VALUES(?,?,?) "
               "ON CONFLICT(reg_no) DO UPDATE SET last_login=excluded.last_login", (reg, ts, ts))
        return jsonify(token=make_token(app, reg, "student"), role="student", reg_no=reg)

    @app.post("/api/admin/login")
    @limited(0.2, 5)
    def admin_login():
        data = request.get_json(silent=True) or {}
        u, p = str(data.get("username", "")), str(data.get("password", ""))
        row = g.db.one("SELECT pw_hash FROM admins WHERE username=?", (u,))
        if not row or not check_password_hash(row["pw_hash"], p):
            return err("bad_credentials", "Wrong username or password.", 401)
        return jsonify(token=make_token(app, u, "admin"), role="admin", reg_no=u)

    @app.get("/api/config")
    @auth()
    def config():
        o, c = settings(g.db)
        day, nm = clock()
        return jsonify(open=o, close=c, grace=GRACE, early=EARLY, max_per_day=MAX_PER_DAY, today=day, now_min=nm)

    @app.get("/api/layout")
    @auth()
    def layout():
        def build():
            out = {}
            for f in FLOORS:
                seats = g.db.q("SELECT id,x,y,has_power,zone,blocked FROM seats WHERE floor=? ORDER BY sort_no", (f,))
                blocked = {s["id"] for s in seats if s["blocked"]}
                lay = g.db.one("SELECT image,decor,data FROM layouts WHERE floor=?", (f,)) or {"image": None, "decor": 0, "data": None}
                if lay.get("data"):
                    obj = json.loads(lay["data"])
                    for s in obj.get("seats", []):
                        s["blocked"] = s.get("id") in blocked
                    out[f] = obj
                    continue
                tables = g.db.q("SELECT x,y,w,h FROM layout_tables WHERE floor=? ORDER BY id", (f,))
                out[f] = dict(seats=[dict(id=s["id"], x=s["x"], y=s["y"], pw=bool(s["has_power"]), z=s["zone"],
                                          blocked=bool(s["blocked"])) for s in seats],
                              tables=tables, img=lay["image"], decor=bool(lay["decor"]))
            return out
        return jsonify(cached(("layout",), 5, build))

    @app.put("/api/admin/layout/<floor>")
    @auth("admin")
    def save_layout(floor):
        if floor not in FLOORS:
            return err("invalid_floor", "Floor must be G or F.", 400)
        data = request.get_json(silent=True) or {}
        L = data.get("layout")
        if L is None:      # older format: seats / tables / img / decor sent separately
            L = dict(seats=data.get("seats"), tables=data.get("tables"), img=data.get("img"), decor=bool(data.get("decor")))
        if not isinstance(L, dict) or not isinstance(L.get("seats"), list) or not isinstance(L.get("tables"), list):
            return err("invalid_layout", "Layout is not valid.", 400)
        if len(L["seats"]) > 500 or len(L["tables"]) > 300 or any(len(L.get(k) or []) > 300 for k in ("zones", "labels")):
            return err("invalid_layout", "Too many items on this floor.", 400)
        img = L.get("img")
        if img is not None and (not isinstance(img, str) or len(img) > 1_500_000):
            return err("image_too_large", "The floor image is too large.", 400)
        seen, rows, clean = set(), [], []
        pat = re.compile(rf"^{floor}\d{{1,3}}$")
        try:
            for s in L["seats"]:
                sid = str(s["id"])
                if not pat.match(sid) or sid in seen:
                    raise ValueError
                seen.add(sid)
                x, y = int(s["x"]), int(s["y"])
                rows.append((sid, floor, int(sid[1:]), x, y, int(bool(s.get("pw"))), s.get("z")))
                c = {k: v for k, v in s.items() if k != "blocked"}
                c.update(id=sid, x=x, y=y)
                clean.append(c)
            trows = [(floor, int(t["x"]), int(t["y"]), int(t["w"]), int(t["h"])) for t in L["tables"]]
        except (KeyError, ValueError, TypeError, AttributeError):
            return err("invalid_layout", "Seat numbers must look like G1, G2 ... and be unique.", 400)
        stored = dict(L, seats=clean)
        blob = json.dumps(stored, separators=(",", ":"))
        if len(blob) > 2_000_000:
            return err("invalid_layout", "The layout is too large.", 400)
        d = g.db
        d.begin()
        try:
            old = {r["id"] for r in d.q("SELECT id FROM seats WHERE floor=?", (floor,))}
            gone = sorted(old - seen)
            if data.get("reset_bookings"):      # e.g. after renumbering: old booking numbers no longer match
                d.x("UPDATE bookings SET status='cancelled' WHERE status IN ('booked','checked_in') "
                    "AND seat_id IN (SELECT id FROM seats WHERE floor=?)", (floor,))
            for sid in gone:
                d.x("UPDATE bookings SET status='cancelled' WHERE seat_id=? AND status IN ('booked','checked_in')", (sid,))
                d.x("DELETE FROM seats WHERE id=?", (sid,))
            d.x("DELETE FROM booking_slots WHERE booking_id IN (SELECT id FROM bookings WHERE status NOT IN ('booked','checked_in'))")
            d.many("INSERT INTO seats(id,floor,sort_no,x,y,has_power,zone) VALUES(?,?,?,?,?,?,?) "
                   "ON CONFLICT(id) DO UPDATE SET sort_no=excluded.sort_no,x=excluded.x,y=excluded.y,"
                   "has_power=excluded.has_power,zone=excluded.zone", rows)
            d.x("DELETE FROM layout_tables WHERE floor=?", (floor,))
            d.many("INSERT INTO layout_tables(floor,x,y,w,h) VALUES(?,?,?,?,?)", trows)
            d.x("DELETE FROM layouts WHERE floor=?", (floor,))
            d.x("INSERT INTO layouts(floor,image,decor,data) VALUES(?,?,?,?)", (floor, img, int(bool(L.get("decor"))), blob))
            d.commit()
        except Exception:
            d.rollback()
            raise
        clear_cache()
        return jsonify(ok=True, seats=len(rows), removed=gone)

    @app.get("/api/availability")
    @auth()
    @limited(10, 30)
    def availability():
        floor = request.args.get("floor", "")
        rng = parse_range({"start": request.args.get("start"), "end": request.args.get("end")})
        if floor not in FLOORS or not rng:
            return err("invalid_input", "Choose a floor and a valid time range.", 400)
        s, e = rng
        day, _ = clock()
        maybe_release(g.db)

        def base():
            seats = g.db.q("SELECT id,blocked FROM seats WHERE floor=? ORDER BY sort_no", (floor,))
            taken = {r["seat_id"] for r in g.db.q(
                f"SELECT DISTINCT seat_id FROM bookings WHERE day=? AND status IN {ACTIVE} AND start_min<? AND end_min>?",
                (day, e, s))}
            return [(x["id"], "blocked" if x["blocked"] else "booked" if x["id"] in taken else "free") for x in seats]
        rows = cached(("av", day, floor, s, e), 2, base)
        mine = {r["seat_id"] for r in g.db.q(
            f"SELECT seat_id FROM bookings WHERE reg_no=? AND day=? AND status IN {ACTIVE} AND start_min<? AND end_min>?",
            (g.user, day, e, s))}
        out = [{"id": i, "status": "mine" if i in mine else st} for i, st in rows]
        n = lambda s: sum(1 for x in out if x["status"] == s)
        return jsonify(seats=out, free=n("free"), booked=n("booked") + n("mine"), mine=n("mine"), blocked=n("blocked"), total=len(out))

    @app.get("/api/free_now")
    @auth()
    @limited(10, 30)
    def free_now():
        """Seats at this very minute (home screen): free, booked right now, blocked."""
        maybe_release(g.db)
        day, nm = clock()

        def build():
            out = {}
            for f in FLOORS:
                total = g.db.one("SELECT COUNT(*) AS n FROM seats WHERE floor=?", (f,))["n"]
                blocked = g.db.one("SELECT COUNT(*) AS n FROM seats WHERE floor=? AND blocked=1", (f,))["n"]
                busy = g.db.one(
                    f"SELECT COUNT(DISTINCT b.seat_id) AS n FROM bookings b JOIN seats s ON s.id=b.seat_id WHERE s.floor=? "
                    f"AND s.blocked=0 AND b.day=? AND b.status IN {ACTIVE} AND b.start_min<=? AND b.end_min>?", (f, day, nm, nm))["n"]
                out[f] = dict(free=total - blocked - busy, booked=busy, blocked=blocked, total=total)
            return out
        out = cached(("free_now", day, nm), 2, build)
        return jsonify(G=out["G"], F=out["F"], free=out["G"]["free"] + out["F"]["free"], total=out["G"]["total"] + out["F"]["total"])

    @app.get("/api/admin/seats_in_use")
    @auth("admin")
    def seats_in_use():
        """Why a seat is not free right now: blocked, or held by a booking (registration number hidden)."""
        day, nm = clock()
        blocked = [r["id"] for r in g.db.q("SELECT id FROM seats WHERE blocked=1 ORDER BY floor, sort_no")]
        rows = g.db.q(f"SELECT id,seat_id,reg_no,start_min,end_min,status FROM bookings WHERE day=? AND status IN {ACTIVE} "
                      "AND start_min<=? AND end_min>? ORDER BY seat_id", (day, nm, nm))
        return jsonify(blocked=blocked, bookings=[dict(id=r["id"], seat=r["seat_id"], who="RA…" + r["reg_no"][-4:],
                       start=r["start_min"], end=r["end_min"], status=r["status"]) for r in rows])

    @app.delete("/api/admin/bookings/<int:bid>")
    @auth("admin")
    def admin_cancel(bid):
        d = g.db
        d.begin()
        try:
            n = d.x(f"UPDATE bookings SET status='cancelled' WHERE id=? AND status IN {ACTIVE}", (bid,))
            d.x("DELETE FROM booking_slots WHERE booking_id=?", (bid,))
            d.commit()
        except Exception:
            d.rollback()
            raise
        clear_cache()
        if not n:
            return err("not_found", "No active booking found.", 404)
        return jsonify(ok=True)

    # ---- Terms of use and Privacy Notice (the admin can edit them) ----
    LEGAL_KINDS = ("terms", "privacy")

    @app.get("/api/legal")
    @limited(5, 20)
    def get_legal():
        def build():
            out = {k: None for k in LEGAL_KINDS}
            for r in g.db.q("SELECT kind,body,version,updated_at FROM legal_docs"):
                if r["kind"] in out:
                    out[r["kind"]] = dict(body=r["body"], version=r["version"], updated=r["updated_at"])
            return out
        return jsonify(cached(("legal",), 5, build))

    @app.put("/api/admin/legal/<kind>")
    @auth("admin")
    def put_legal(kind):
        body = ((request.get_json(silent=True) or {}).get("body") or "").replace("\r\n", "\n").strip()
        if kind not in LEGAL_KINDS:
            return err("not_found", "Unknown document.", 404)
        if len(body) < 20 or len(body) > 20000:
            return err("invalid_input", "The text must be between 20 and 20,000 characters.", 400)
        g.db.x("INSERT INTO legal_docs(kind,body,version,updated_at) VALUES(?,?,1,?) ON CONFLICT(kind) DO UPDATE SET "
               "body=excluded.body, version=legal_docs.version+1, updated_at=excluded.updated_at",
               (kind, body, now_local().isoformat(timespec="seconds")))
        clear_cache()
        return jsonify(ok=True)

    @app.delete("/api/admin/legal/<kind>")
    @auth("admin")
    def reset_legal(kind):
        if kind not in LEGAL_KINDS:
            return err("not_found", "Unknown document.", 404)
        g.db.x("DELETE FROM legal_docs WHERE kind=?", (kind,))
        clear_cache()
        return jsonify(ok=True)

    @app.post("/api/bookings")
    @auth()
    @limited(5, 10)
    def book():
        if g.role != "student":
            return err("admin_cannot_book", "Admins cannot reserve seats.", 403)
        data = request.get_json(silent=True) or {}
        rng, seat = parse_range(data), str(data.get("seat", ""))
        if not rng:
            return err("invalid_time", "Choose a valid start and end time (15-minute steps, at least 30 minutes).", 400)
        s, e = rng
        d, reg = g.db, g.user
        day, nm = clock()
        o, c = settings(d)
        if s < o * 60 or e > c * 60:
            return err("closed", "Bookings are only possible between opening and closing time.", 403)
        if e <= nm or s < (nm // STEP) * STEP:
            return err("time_passed", "That time has already passed.", 400)
        st = d.one("SELECT blocked FROM seats WHERE id=?", (seat,))
        if not st:
            return err("seat_not_found", "That seat does not exist.", 404)
        if st["blocked"]:
            return err("seat_blocked", "That seat is blocked by the library.", 409)
        slots = range(s // STEP, e // STEP)
        d.begin(lock_key=reg)
        try:
            cnt = d.one(f"SELECT COUNT(*) AS c FROM bookings WHERE reg_no=? AND day=? AND status IN {ACTIVE}", (reg, day))["c"]
            own = d.one(f"SELECT seat_id,start_min,end_min FROM bookings WHERE reg_no=? AND day=? AND status IN {ACTIVE} "
                        "AND start_min<? AND end_min>?", (reg, day, e, s))
            if own:
                d.rollback()
                return err("already_reserved", "You already have a reserved seat at this time.", 409)
            if cnt >= MAX_PER_DAY:
                d.rollback()
                return err("limit_reached", "Limit: 2 bookings per day.", 409)
            bid = d.q("INSERT INTO bookings(reg_no,seat_id,day,start_min,end_min,status,created_at) "
                      "VALUES(?,?,?,?,?,'booked',?) RETURNING id", (reg, seat, day, s, e, now_local().isoformat()))[0]["id"]
            d.many("INSERT INTO booking_slots(booking_id,seat_id,reg_no,day,slot) VALUES(?,?,?,?,?)",
                   [(bid, seat, reg, day, sl) for sl in slots])
            d.commit()
        except dbm.IntegrityError:
            d.rollback()
            own = d.one(f"SELECT 1 AS x FROM bookings WHERE reg_no=? AND day=? AND status IN {ACTIVE} AND start_min<? AND end_min>?",
                        (reg, day, e, s))
            if own:
                return err("already_reserved", "You already have a reserved seat at this time.", 409)
            return err("seat_taken", "Seat already booked.", 409)
        except Exception:
            d.rollback()
            raise
        clear_cache()
        return jsonify(id=bid, seat_id=seat, day=day, start_min=s, end_min=e, status="booked"), 201

    @app.get("/api/bookings/me")
    @auth()
    def my_bookings():
        maybe_release(g.db)
        cutoff = (now_local() - timedelta(days=30)).strftime("%Y-%m-%d")
        rows = g.db.q("SELECT id,seat_id,day,start_min,end_min,status,checked_in_at FROM bookings "
                      "WHERE reg_no=? AND day>=? ORDER BY day DESC,start_min DESC", (g.user, cutoff))
        return jsonify(bookings=rows)

    @app.delete("/api/bookings/<int:bid>")
    @auth()
    def cancel(bid):
        d = g.db
        d.begin(lock_key=g.user)
        try:
            n = d.x(f"UPDATE bookings SET status='cancelled' WHERE id=? AND reg_no=? AND status IN {ACTIVE}", (bid, g.user))
            if n:
                d.x("DELETE FROM booking_slots WHERE booking_id=?", (bid,))
            d.commit()
        except Exception:
            d.rollback()
            raise
        clear_cache()
        if not n:
            return err("not_found", "No active booking found.", 404)
        return jsonify(ok=True)

    @app.post("/api/checkin")
    @auth()
    @limited(3, 6)
    def checkin():
        if g.role != "student":
            return err("forbidden", "Only students check in.", 403)
        data = request.get_json(silent=True) or {}
        seat, tok = str(data.get("seat", "")), str(data.get("token", ""))
        if not hmac.compare_digest(seat_token(app, seat), tok):
            return err("invalid_code", "This QR code is not valid.", 400)
        d = g.db
        day, nm = clock()
        d.begin(lock_key=g.user)
        try:
            rows = d.q("SELECT id,seat_id,start_min,end_min,status FROM bookings WHERE reg_no=? AND day=? AND end_min>? "
                       "AND status IN ('booked','checked_in','released') ORDER BY start_min", (g.user, day, nm))
            same = [r for r in rows if r["seat_id"] == seat and r["status"] != "released"]
            result, b = "none", None
            if same:
                b = same[0]
                if b["status"] == "checked_in":
                    result = "done"
                elif nm < b["start_min"] - EARLY:
                    result = "early"
                elif nm >= b["start_min"] + GRACE:
                    d.x("UPDATE bookings SET status='released' WHERE id=?", (b["id"],))
                    d.x("DELETE FROM booking_slots WHERE booking_id=?", (b["id"],))
                    result = "rel"
                else:
                    d.x("UPDATE bookings SET status='checked_in',checked_in_at=? WHERE id=?", (now_local().isoformat(), b["id"]))
                    result = "ok"
            elif any(r["seat_id"] == seat and r["status"] == "released" for r in rows):
                result = "rel"
            else:
                other = [r for r in rows if r["status"] != "released"]
                if other:
                    result, b = "other", other[0]
            d.commit()
        except Exception:
            d.rollback()
            raise
        clear_cache()
        return jsonify(result=result, seat=seat, booking=({k: b[k] for k in ("id", "seat_id", "start_min", "end_min")} if b else None))

    # ----- admin -----
    @app.put("/api/admin/seats/<seat>/blocked")
    @auth("admin")
    def block(seat):
        v = int(bool((request.get_json(silent=True) or {}).get("blocked", True)))
        if not g.db.x("UPDATE seats SET blocked=? WHERE id=?", (v, seat)):
            return err("seat_not_found", "That seat does not exist.", 404)
        clear_cache()
        return jsonify(ok=True, blocked=bool(v))

    @app.put("/api/admin/settings")
    @auth("admin")
    def put_settings():
        data = request.get_json(silent=True) or {}
        try:
            o, c = int(data["open"]), int(data["close"])
        except (KeyError, ValueError, TypeError):
            return err("invalid_input", "Send open and close hours.", 400)
        if not (0 <= o < c <= 24):
            return err("invalid_input", "Opening hour must be before closing hour.", 400)
        for k, v in (("open", o), ("close", c)):
            g.db.x("UPDATE settings SET value=? WHERE key=?", (str(v), k))
        return jsonify(open=o, close=c)

    @app.get("/api/admin/stats")
    @auth("admin")
    def stats():
        floor = request.args.get("floor", "G")
        day, _ = clock()
        o, c = settings(g.db)
        out = []
        for h in range(o, c):
            n = g.db.one("SELECT COUNT(*) AS n FROM bookings WHERE day=? AND status IN ('booked','checked_in','ended') "
                         "AND seat_id IN (SELECT id FROM seats WHERE floor=?) AND start_min<? AND end_min>?",
                         (day, floor, (h + 1) * 60, h * 60))["n"]
            out.append(dict(hour=h, booked=n))
        total = g.db.one("SELECT COUNT(*) AS n FROM seats WHERE floor=?", (floor,))["n"]
        return jsonify(floor=floor, seats=total, hours=out)

    @app.get("/api/busy")
    @auth()
    def busy():
        day, _ = clock()
        o, c = settings(g.db)

        def build():
            out = {}
            for f in FLOORS:
                hours = {}
                for h in range(o, c):
                    hours[str(h)] = g.db.one(
                        "SELECT COUNT(*) AS n FROM bookings WHERE day=? AND status IN ('booked','checked_in','ended') "
                        "AND seat_id IN (SELECT id FROM seats WHERE floor=?) AND start_min<? AND end_min>?",
                        (day, f, (h + 1) * 60, h * 60))["n"]
                out[f] = dict(total=g.db.one("SELECT COUNT(*) AS n FROM seats WHERE floor=?", (f,))["n"], hours=hours)
            return out
        return jsonify(cached(("busy", day, o, c), 10, build))

    @app.delete("/api/me")
    @auth()
    def delete_me():
        if g.role != "student":
            return err("forbidden", "Only students have personal data to delete.", 403)
        d = g.db
        d.begin(lock_key=g.user)
        try:
            d.x("DELETE FROM booking_slots WHERE reg_no=?", (g.user,))
            d.x("DELETE FROM bookings WHERE reg_no=?", (g.user,))
            d.x("DELETE FROM users WHERE reg_no=?", (g.user,))
            d.commit()
        except Exception:
            d.rollback()
            raise
        clear_cache()
        return jsonify(ok=True)

    @app.get("/api/admin/qr")
    @auth("admin")
    def qr_list():
        floor, base = request.args.get("floor", "G"), request.args.get("base", "").split("#")[0]
        seats = g.db.q("SELECT id FROM seats WHERE floor=? ORDER BY sort_no", (floor,))
        return jsonify(codes=[dict(seat=s["id"], token=seat_token(app, s["id"]),
                                   url=f"{base}#checkin={s['id']}.{seat_token(app, s['id'])}") for s in seats])


app = None
if os.environ.get("LIBSEAT_NO_AUTOSTART") != "1":
    app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")), debug=os.environ.get("APP_ENV") != "production")
