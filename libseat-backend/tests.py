"""Run:  python tests.py     (uses a temporary SQLite database)"""
import os, sys, tempfile, threading, unittest
from datetime import datetime
os.environ.update(LIBSEAT_NO_AUTOSTART="1", DEV_LOGIN="1", ADMIN_PASSWORD="admin-pass", RATE_LIMIT="0", SECRET_KEY="test-secret")
import app as A

TMP = tempfile.mkdtemp()
APP = A.create_app("sqlite:///" + os.path.join(TMP, "t.db"))
NOW = [datetime(2026, 10, 28, 10, 0, tzinfo=A.TZ)]
A.now_local = lambda: NOW[0]


def at(h, m=0):
    NOW[0] = datetime(2026, 10, 28, h, m, tzinfo=A.TZ)
    A._last_release[0] = 0
    A.clear_cache()


def reg(n):
    return "RA" + str(n).zfill(13)


class T(unittest.TestCase):
    def setUp(self):
        self.c = APP.test_client()
        at(10, 0)
        d = A.dbm.connect()
        d.x("DELETE FROM booking_slots"); d.x("DELETE FROM bookings"); d.x("UPDATE seats SET blocked=0")
        d.close()

    def login(self, n=1):
        r = self.c.post("/api/login", json={"reg_no": reg(n)})
        self.assertEqual(r.status_code, 200)
        return {"Authorization": "Bearer " + r.json["token"]}

    def admin(self):
        r = self.c.post("/api/admin/login", json={"username": "admin", "password": "admin-pass"})
        self.assertEqual(r.status_code, 200)
        return {"Authorization": "Bearer " + r.json["token"]}

    def book(self, h, seat, s, e):
        return self.c.post("/api/bookings", json={"seat": seat, "start": s, "end": e}, headers=h)

    def test_login_rules(self):
        self.assertEqual(self.c.post("/api/login", json={"reg_no": "RA123"}).status_code, 400)
        self.assertEqual(self.c.post("/api/login", json={"reg_no": "AB2511003010123"}).status_code, 400)
        self.assertEqual(self.c.post("/api/login", json={"reg_no": "ra2511003010123"}).status_code, 200)
        self.assertEqual(self.c.post("/api/admin/login", json={"username": "admin", "password": "x"}).status_code, 401)
        self.assertEqual(self.c.get("/api/config").status_code, 401)

    def test_book_happy_and_rules(self):
        h = self.login(1)
        r = self.book(h, "G5", 630, 720)
        self.assertEqual(r.status_code, 201, r.json)
        self.assertEqual(self.book(h, "G6", 660, 720).json["error"], "already_reserved")   # overlap, other seat
        self.assertEqual(self.book(h, "G5", 630, 720).json["error"], "already_reserved")
        self.assertEqual(self.book(h, "G6", 780, 840).status_code, 201)                      # different time ok
        self.assertEqual(self.book(h, "G7", 900, 960).json["error"], "limit_reached")        # 3rd booking today
        h2 = self.login(2)
        self.assertEqual(self.book(h2, "G5", 660, 720).json["error"], "seat_taken")
        self.assertEqual(self.book(h2, "G5", 600, 615).json["error"], "invalid_time")        # < 30 min
        self.assertEqual(self.book(h2, "G5", 601, 720).json["error"], "invalid_time")        # not 15-min step
        self.assertEqual(self.book(h2, "G5", 360, 420).json["error"], "closed")              # before 7 AM
        self.assertEqual(self.book(h2, "G5", 1380, 1440).json["error"], "closed")            # after 11 PM
        self.assertEqual(self.book(h2, "G5", 480, 540).json["error"], "time_passed")         # already over
        self.assertEqual(self.book(h2, "G99", 630, 690).json["error"], "seat_not_found")
        self.assertEqual(self.book(h2, "G5", 780, 1320).status_code, 201)                    # no maximum stay

    def test_admin_cannot_book_and_blocking(self):
        a = self.admin()
        self.assertEqual(self.book(a, "G5", 630, 690).json["error"], "admin_cannot_book")
        self.assertEqual(self.c.put("/api/admin/seats/G5/blocked", json={"blocked": True}, headers=a).status_code, 200)
        h = self.login(3)
        self.assertEqual(self.book(h, "G5", 630, 690).json["error"], "seat_blocked")
        self.assertEqual(self.c.put("/api/admin/seats/G5/blocked", json={"blocked": True}, headers=h).status_code, 403)

    def test_cancel_frees_seat_and_availability(self):
        h1, h2 = self.login(1), self.login(2)
        b = self.book(h1, "G3", 630, 690).json
        av = self.c.get("/api/availability?floor=G&start=630&end=690", headers=h2).json
        self.assertEqual({s["id"]: s["status"] for s in av["seats"]}["G3"], "booked")
        av1 = self.c.get("/api/availability?floor=G&start=630&end=690", headers=h1).json
        self.assertEqual({s["id"]: s["status"] for s in av1["seats"]}["G3"], "mine")
        self.assertEqual(self.c.delete(f"/api/bookings/{b['id']}", headers=h1).status_code, 200)
        self.assertEqual(self.book(h2, "G3", 630, 690).status_code, 201)
        self.assertEqual(self.c.delete(f"/api/bookings/{b['id']}", headers=h1).status_code, 404)

    def test_cannot_cancel_another_students_booking(self):
        owner, other = self.login(1), self.login(2)
        booking = self.book(owner, "G4", 630, 690).json

        self.assertEqual(self.c.delete(f"/api/bookings/{booking['id']}", headers=other).status_code, 404)
        self.assertEqual(self.book(other, "G4", 630, 690).status_code, 409)

    def test_race_same_seat(self):
        res = []
        def go(n):
            h = self.login(100 + n)
            res.append(self.book(h, "G10", 630, 690).status_code)
        th = [threading.Thread(target=go, args=(i,)) for i in range(30)]
        [t.start() for t in th]; [t.join() for t in th]
        self.assertEqual(res.count(201), 1, res)
        self.assertEqual(res.count(409), 29, res)

    def test_race_same_student(self):
        h = self.login(500)
        res = []
        def go(i):
            res.append(self.book(h, f"G{20 + i}", 630, 690).status_code)
        th = [threading.Thread(target=go, args=(i,)) for i in range(10)]
        [t.start() for t in th]; [t.join() for t in th]
        self.assertEqual(res.count(201), 1, res)

    def test_checkin_and_release(self):
        h = self.login(7)
        self.book(h, "G12", 630, 690)                       # starts 10:30, now 10:00
        tok = lambda s: A.seat_token(APP, s)
        ci = lambda s, t=None: self.c.post("/api/checkin", json={"seat": s, "token": t or tok(s)}, headers=h).json
        self.assertEqual(ci("G12", "0" * 16)["error"], "invalid_code")
        at(10, 5);  self.assertEqual(ci("G12")["result"], "early")      # opens at 10:15
        self.assertEqual(ci("G13")["result"], "other")
        at(10, 20); self.assertEqual(ci("G12")["result"], "ok")
        self.assertEqual(ci("G12")["result"], "done")
        # no-show: booked 12:00-13:00, never checked in
        self.book(h, "G14", 720, 780)
        h2 = self.login(8)
        at(12, 19); A.release_unclaimed(A.dbm.connect())
        self.assertEqual(self.book(h2, "G14", 750, 780).json["error"], "seat_taken")
        at(12, 20); d = A.dbm.connect(); n = A.release_unclaimed(d); d.close()
        self.assertEqual(n, 1)
        self.assertEqual(ci("G14")["result"], "rel")
        self.assertEqual(self.book(h2, "G14", 750, 780).status_code, 201)   # seat is free again
        none = self.c.post("/api/checkin", json={"seat": "G30", "token": tok("G30")}, headers=self.login(9)).json
        self.assertEqual(none["result"], "none")

    def test_scan_after_grace_releases_even_without_cron(self):
        h = self.login(11)
        self.book(h, "G16", 720, 780)
        at(12, 25)
        r = self.c.post("/api/checkin", json={"seat": "G16", "token": A.seat_token(APP, "G16")}, headers=h).json
        self.assertEqual(r["result"], "rel")

    def test_layout_edit_cancels_removed_seats(self):
        a, h = self.admin(), self.login(21)
        self.book(h, "G31", 630, 690)
        lay = self.c.get("/api/layout", headers=h).json["G"]
        seats = [s for s in lay["seats"] if s["id"] != "G31"] + [dict(id="G40", x=50, y=60, pw=True)]
        r = self.c.put("/api/admin/layout/G", json=dict(seats=seats, tables=lay["tables"], decor=True), headers=a)
        self.assertEqual(r.status_code, 200, r.json)
        self.assertEqual(r.json["removed"], ["G31"])
        mine = self.c.get("/api/bookings/me", headers=h).json["bookings"]
        self.assertEqual(mine[0]["status"], "cancelled")
        ids = [s["id"] for s in self.c.get("/api/layout", headers=h).json["G"]["seats"]]
        self.assertIn("G40", ids); self.assertNotIn("G31", ids)
        bad = self.c.put("/api/admin/layout/G", json=dict(seats=[dict(id="F1", x=1, y=1)], tables=[]), headers=a)
        self.assertEqual(bad.status_code, 400)
        self.assertEqual(self.c.put("/api/admin/layout/G", json=dict(seats=seats, tables=[]), headers=h).status_code, 403)

    def test_admin_tools(self):
        a = self.admin()
        qr = self.c.get("/api/admin/qr?floor=F&base=https://x.edu/app/", headers=a).json["codes"]
        self.assertEqual(len(qr), 32)
        self.assertTrue(qr[0]["url"].startswith("https://x.edu/app/#checkin=F1."))
        self.assertEqual(self.c.get("/api/admin/stats?floor=G", headers=a).status_code, 200)
        self.assertEqual(self.c.put("/api/admin/settings", json={"open": 7, "close": 23}, headers=a).status_code, 200)
        self.assertEqual(self.c.put("/api/admin/settings", json={"open": 9, "close": 8}, headers=a).status_code, 400)

    def test_busy_delete_me_and_reset(self):
        h, a = self.login(31), self.admin()
        self.book(h, "G2", 630, 690)
        busy = self.c.get("/api/busy", headers=h).json
        self.assertEqual(busy["G"]["total"], 32)
        self.assertEqual(busy["G"]["hours"]["10"], 1)
        self.assertEqual(self.c.delete("/api/me", headers=a).status_code, 403)
        self.assertEqual(self.c.delete("/api/me", headers=h).status_code, 200)
        self.assertEqual(self.c.get("/api/bookings/me", headers=h).json["bookings"], [])
        h2 = self.login(32)
        self.book(h2, "G3", 630, 690)
        lay = self.c.get("/api/layout", headers=h2).json["G"]
        r = self.c.put("/api/admin/layout/G", json=dict(seats=lay["seats"], tables=lay["tables"], reset_bookings=True), headers=a)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.c.get("/api/bookings/me", headers=h2).json["bookings"][0]["status"], "cancelled")

    def test_layout_keeps_zones_labels_and_size(self):
        a, h = self.admin(), self.login(41)
        lay = self.c.get("/api/layout", headers=h).json["F"]
        full = dict(lay, w=420, h=600, name="Main Hall",
                    zones=[dict(x=10, y=10, w=140, h=100, name="Quiet", c=0)],
                    labels=[dict(x=30, y=40, t="Entrance")])
        full["seats"] = [dict(s, g="grp1") for s in lay["seats"]]
        r = self.c.put("/api/admin/layout/F", json=dict(layout=full), headers=a)
        self.assertEqual(r.status_code, 200, r.json)
        self.c.put("/api/admin/seats/F3/blocked", json={"blocked": True}, headers=a)
        got = self.c.get("/api/layout", headers=h).json["F"]
        self.assertEqual((got["w"], got["h"], got["name"]), (420, 600, "Main Hall"))
        self.assertEqual(got["zones"][0]["name"], "Quiet")
        self.assertEqual(got["labels"][0]["t"], "Entrance")
        self.assertTrue(all(s["g"] == "grp1" for s in got["seats"]))
        self.assertTrue(next(s for s in got["seats"] if s["id"] == "F3")["blocked"])
        # saving again must not unblock the seat
        self.c.put("/api/admin/layout/F", json=dict(layout=got), headers=a)
        self.assertTrue(next(s for s in self.c.get("/api/layout", headers=h).json["F"]["seats"] if s["id"] == "F3")["blocked"])
        bad = dict(full, seats=[dict(id="G1", x=1, y=1)])
        self.assertEqual(self.c.put("/api/admin/layout/F", json=dict(layout=bad), headers=a).status_code, 400)

    def test_fresh_library_shows_all_seats_free(self):
        h = self.login(51)
        r = self.c.get("/api/availability?floor=G&start=600&end=660", headers=h).json
        f = self.c.get("/api/availability?floor=F&start=600&end=660", headers=h).json
        self.assertEqual((r["free"], f["free"], r["total"] + f["total"]), (32, 32, 64))
        self.book(h, "G4", 600, 660)
        self.c.put("/api/admin/seats/G9/blocked", json={"blocked": True}, headers=self.admin())
        r = self.c.get("/api/availability?floor=G&start=600&end=660", headers=h).json
        self.assertEqual((r["free"], r["booked"], r["mine"], r["blocked"]), (30, 1, 1, 1))

    def test_cron_release_endpoint(self):
        os.environ["CRON_KEY"] = "cron-secret"
        self.assertEqual(self.c.post("/api/internal/release").status_code, 403)
        self.assertEqual(self.c.post("/api/internal/release", headers={"X-Cron-Key": "wrong"}).status_code, 403)
        h = self.login(52)
        self.book(h, "G6", 720, 780)
        at(12, 25)
        r = self.c.post("/api/internal/release", headers={"X-Cron-Key": "cron-secret"})
        self.assertEqual((r.status_code, r.json["released"]), (200, 1))

    def test_free_now_counts_this_minute_only(self):
        h, a = self.login(1), self.admin()
        now = lambda: self.c.get("/api/free_now", headers=h).json
        self.assertEqual(now()["free"], 64)
        self.assertEqual(self.book(h, "G1", 615, 675).status_code, 201)       # 10:15-11:15, it is 10:00
        self.assertEqual(now()["free"], 64)                                   # still free right now
        at(10, 20)
        r = now()
        self.assertEqual((r["free"], r["G"]["booked"]), (63, 1))              # booking has started
        self.c.put("/api/admin/seats/G2/blocked", json={"blocked": True}, headers=a)
        r = now()
        self.assertEqual((r["free"], r["G"]["blocked"], r["G"]["total"]), (62, 1, 32))

    def test_admin_sees_why_a_seat_is_not_free(self):
        h, a = self.login(1), self.admin()
        self.assertEqual(self.book(h, "G3", 600, 660).status_code, 201)
        self.c.put("/api/admin/seats/G4/blocked", json={"blocked": True}, headers=a)
        r = self.c.get("/api/admin/seats_in_use", headers=a).json
        self.assertEqual(r["blocked"], ["G4"])
        self.assertEqual([(b["seat"], b["who"]) for b in r["bookings"]], [("G3", "RA…0001")])
        self.assertEqual(self.c.get("/api/admin/seats_in_use", headers=h).status_code, 403)
        self.assertEqual(self.c.delete("/api/admin/bookings/" + str(r["bookings"][0]["id"]), headers=a).status_code, 200)
        self.assertEqual(self.c.get("/api/free_now", headers=h).json["free"], 63)

    def test_local_website_is_allowed_even_if_origins_are_restricted(self):
        old = os.environ.get("ALLOWED_ORIGINS")
        os.environ["ALLOWED_ORIGINS"] = "https://library.example.edu"
        try:
            ok = self.c.get("/healthz", headers={"Origin": "http://localhost:8080"})
            self.assertEqual(ok.headers.get("Access-Control-Allow-Origin"), "http://localhost:8080")
            bad = self.c.get("/healthz", headers={"Origin": "https://evil.example"})
            self.assertIsNone(bad.headers.get("Access-Control-Allow-Origin"))
        finally:
            os.environ.pop("ALLOWED_ORIGINS", None) if old is None else os.environ.update(ALLOWED_ORIGINS=old)

    def test_admin_can_edit_terms_and_privacy(self):
        h, a = self.login(1), self.admin()
        self.c.delete("/api/admin/legal/terms", headers=a); self.c.delete("/api/admin/legal/privacy", headers=a)
        self.assertEqual(self.c.get("/api/legal").json, {"terms": None, "privacy": None})          # public, nothing custom yet
        text = "## Our rules\nUse your own seat.\n- Be quiet\n- Check in on time"
        self.assertEqual(self.c.put("/api/admin/legal/terms", json={"body": text}, headers=h).status_code, 403)   # students cannot
        self.assertEqual(self.c.put("/api/admin/legal/terms", json={"body": text}, headers=a).status_code, 200)
        r = self.c.get("/api/legal").json
        self.assertEqual((r["terms"]["body"], r["terms"]["version"], r["privacy"]), (text, 1, None))
        self.c.put("/api/admin/legal/terms", json={"body": text + "\nMore."}, headers=a)
        self.assertEqual(self.c.get("/api/legal").json["terms"]["version"], 2)                       # each save bumps the version
        self.assertEqual(self.c.put("/api/admin/legal/terms", json={"body": "short"}, headers=a).status_code, 400)
        self.assertEqual(self.c.put("/api/admin/legal/other", json={"body": text}, headers=a).status_code, 404)
        self.assertEqual(self.c.delete("/api/admin/legal/terms", headers=a).status_code, 200)         # back to the built-in text
        self.assertIsNone(self.c.get("/api/legal").json["terms"])

    def test_postgres_saving_logic(self):
        """PostgreSQL only saves on commit. Check single writes are saved and begin..commit saves once."""
        import db as D
        calls = []
        class Cur:
            description = None; rowcount = 1
            def execute(self, *a): calls.append("sql")
            def executemany(self, *a): calls.append("sql")
            def close(self): pass
        class Conn:
            def commit(self): calls.append("commit")
            def rollback(self): calls.append("rollback")
        class FakeDB(D.DB):
            def _cur(self): return Cur()
        old = D.IS_PG
        D.IS_PG = True
        try:
            d = FakeDB(Conn())
            d.x("UPDATE seats SET blocked=1 WHERE id=?", ("G1",))
            self.assertEqual(calls, ["sql", "commit"])
            calls.clear(); d.q("SELECT 1 AS x")
            self.assertEqual(calls, ["sql"])
            calls.clear(); d.begin(); d.x("INSERT INTO t VALUES(?)", (1,)); d.many("INSERT INTO t VALUES(?)", [(2,)])
            self.assertNotIn("commit", calls)
            d.commit()
            self.assertEqual(calls[-1], "commit")
        finally:
            D.IS_PG = old

    def test_retention_job(self):
        d = A.dbm.connect()
        d.x("INSERT INTO bookings(reg_no,seat_id,day,start_min,end_min,status) VALUES('RA0000000000001','G1','2026-08-01',600,660,'ended')")
        d.begin(); n = d.x("DELETE FROM bookings WHERE day<?", ("2026-09-28",)); d.commit(); d.close()
        self.assertEqual(n, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
