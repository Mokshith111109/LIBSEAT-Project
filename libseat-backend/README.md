# LibSeat backend (Python + Flask)

> **Start with `../GUIDE.md`: viewing on a phone, editing the Terms and Privacy Notice, and saving data in Supabase.**

Handles login, the floor layout, availability, bookings (no double booking), QR check-in, and the 20-minute automatic release.
Runs on SQLite for testing and PostgreSQL for production.

## Files
- `app.py` – the API (all rules live here, never in the browser)
- `db.py` – database layer (SQLite for development, PostgreSQL for production)
- `release.py` – job run every minute: cancels bookings not checked in within 20 minutes, deletes data older than 30 days
- `tests.py` – 20 tests, including 30 people booking the same seat at once
- `api-client.js` – drop-in code for your front-end
- `schema_postgres.sql`, `.env.example`, `gunicorn.conf.py`, `Dockerfile`, `docker-compose.yml`, `nginx.conf.example`, `loadtest.k6.js`

## Step 1 – Run it on your computer
1. Install Python 3.11 or newer (if you already use Python in VS Code, just open this folder in VS Code and use its terminal).
2. In this folder:
   ```
   python -m venv venv
   venv\Scripts\activate          (Windows)   |   source venv/bin/activate   (Mac/Linux)
   pip install -r requirements.txt
   ```
3. Run the tests: `python tests.py` (all 20 should say ok).
4. Start the server (testing mode only). In the VS Code terminal on Windows (PowerShell) use these three lines instead:
   ```
   $env:DEV_LOGIN="1"; $env:ADMIN_PASSWORD="choose-one"; python app.py
   ```
   If PowerShell refuses to activate the venv, run `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` first, or open a Command Prompt terminal instead. Other systems:
   ```
   set DEV_LOGIN=1 & set ADMIN_PASSWORD=choose-one & python app.py        (Windows)
   DEV_LOGIN=1 ADMIN_PASSWORD=choose-one python app.py                    (Mac/Linux)
   ```
   Open http://localhost:5000/healthz – you should see {"ok": true}.
5. Try it:
   ```
   curl -X POST localhost:5000/api/login -H "Content-Type: application/json" -d "{\"reg_no\":\"RA2511003010123\"}"
   ```
   Copy the token, then call `/api/config`, `/api/layout`, `/api/availability?floor=G&start=600&end=660` with `-H "Authorization: Bearer TOKEN"`.

## Step 2 – Connect your front-end
Add `api-client.js` and replace the places in `app.js` that read or write `localStorage` for bookings, layout and settings with the `Api.*` calls (the comments in the file show which call replaces what). The screens, messages and error pages stay the same. Keep the time, hours and rules on the server only.

## Step 3 – Release job
Without it, unclaimed seats are still released the next time anyone loads availability or scans a QR, but run it every minute anyway:
```
* * * * * cd /path/to/libseat-backend && /path/to/venv/bin/python release.py
```

## Step 4 – Go to production (for the real library)
1. **PostgreSQL:** install it (or use a managed one in an Indian region). Create a database and user. Set `DATABASE_URL`. The tables are created automatically (also in `schema_postgres.sql`).
2. **Secrets:** copy `.env.example` to `.env`; set long random `SECRET_KEY` and `QR_SECRET`, a strong `ADMIN_PASSWORD`, your site in `ALLOWED_ORIGINS`, and `APP_ENV=production`. **Do not set `DEV_LOGIN`.**
3. **Student login (the most important step):** `DEV_LOGIN` lets anyone claim any registration number. Ask the college IT team for their single sign-on (Google Workspace, Microsoft or their own). Change the `/api/login` route so it accepts a verified sign-in and takes the registration number from the college's response, never from the user's typing.
4. **Run it:** `gunicorn -c gunicorn.conf.py app:app` (or `docker compose up --build`).
5. **HTTPS:** put nginx (see `nginx.conf.example`) or a cloud load balancer in front, with a free Let's Encrypt certificate.
6. **Front-end files:** upload `index.html`, `style.css`, `app.js` to the same server or any static host.
7. **QR codes:** log in as admin, call `/api/admin/qr?floor=G&base=https://your-site/` and print each `url` as a QR (the admin QR generator in the front-end can do this). The link carries a signature, so people cannot invent codes.
8. **Backups and monitoring:** daily database backups (test a restore), and alerts on errors and slow responses.

## Putting it online
See `DEPLOY.md` for the step-by-step guide (database, backend and website on free services, nothing stored on your computer).

## Step 5 – For 1 lakh (100,000) users and 20,000 requests at once
- **Storing 1 lakh users** is small (about 10–20 MB). Bookings are deleted after 30 days by `release.py`, so the data stays small.
- **20,000 requests at once** is mostly reading availability. The server caches availability for 2 seconds, so the database sees few queries. Writes are limited by the number of seats: only one booking can win each seat, and everyone else gets a quick 409.
- **Estimates (measure them, do not trust them):** if one server handles a request in about 20 ms with 32 workers/threads, it manages roughly 1,000–1,500 requests/second. So 20,000 requests arriving in a second or two needs about 10–15 servers behind the load balancer, or a short queue with a "too busy, try again" message (already supported as error 429).
- **Do this order:** (1) PostgreSQL with a connection pooler such as PgBouncer, (2) 2–4 API servers, (3) put the cache in Redis once you have more than one server, (4) load test with `loadtest.k6.js`, (5) fix the slowest endpoint, repeat. Generating 20,000 simulated users needs several test machines or a cloud load-testing service.
- Phones should refresh seat availability at random 8–12 second intervals, not at the same instant.

## Testing at any time of day
The library is closed late at night, so bookings fail then. For testing only, set a pretend time before starting the server (it is ignored when `APP_ENV=production`):
`$env:FAKE_NOW="2026-10-31T10:00"` (PowerShell) or `FAKE_NOW=2026-10-31T10:00` (Mac/Linux).

## Rules the server enforces
Registration number `RA` + 13 digits · bookings for today only · 15-minute steps, at least 30 minutes, no maximum · opening hours from settings (default 7 AM–11 PM) · one seat per person at any time and 2 bookings per day · a seat can only be held by one person at a time · admins cannot book · check-in opens 15 minutes before the start and the booking is cancelled 20 minutes after the start if not checked in.

## What is tested and what is not
Tested here with SQLite: all 20 tests (rules, check-in, release, layout edit, and races with 30 simultaneous bookings).
**Not tested here:** PostgreSQL, Docker, nginx, gunicorn and the k6 load test, and your real college login. Test each of them on your own setup before launch.

## Privacy
Collect only the registration number and bookings. Get the college's written approval, and follow India's data protection rules (users under 18 need verifiable parental consent). Limit who can see the database.
