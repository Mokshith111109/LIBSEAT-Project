# Put it online (so nothing is stored on your computer)

The data must live on a server that is always on. You need three things: a **database**, the **backend**, and the **website**.

> Free plans change often. Check each company's page before you start. At the time I looked (2026): Render's free web service goes to sleep after 15 minutes without visitors and takes about a minute to wake up, and its free disk is wiped on every restart (so the SQLite file would be lost - you MUST use PostgreSQL). Render's own free database is deleted after 30 days. Neon's free PostgreSQL has no time limit (0.5 GB), and Supabase's free projects pause after a week of no activity.

**Not tested here:** the PostgreSQL path, Render and Neon. I tested everything with SQLite. Test each step below and tell me what you see.

## Step 1. Put your code on GitHub
1. Make a free account at github.com and create a repository (call it `rysil`).
2. Upload the two folders: `libseat-backend` (rename it `backend`) and `website`.
3. Do NOT upload `.env`, `venv`, or any `.db` file.

## Step 2. Make the database (Supabase)
1. Sign up at supabase.com (free), press **New project**, choose a region near you, and set a database password.
2. Press **Connect**, choose **Transaction pooler**, and copy the URI. Replace `[YOUR-PASSWORD]` with your password. Keep it secret.
3. Use that string as `DATABASE_URL` in Step 3. (More detail in `../GUIDE.md`.)

## Step 3. Put the backend online (Render)
1. Sign up at render.com with your GitHub account. Click **New > Web Service** and pick your repository.
2. Set **Root Directory** to `backend`. **Build command:** `pip install -r requirements.txt`. **Start command:** `gunicorn -c gunicorn.conf.py app:app`.
3. Under **Environment**, add these variables:
   - `DATABASE_URL` = the Supabase connection string
   - `SECRET_KEY` = a long random text (type 40 random letters and numbers)
   - `QR_SECRET` = another long random text
   - `ADMIN_PASSWORD` = a strong password for the librarian
   - `APP_ENV` = `production`
   - `ALLOWED_ORIGINS` = your website address from Step 4 (add it after Step 4, then redeploy)
   - `TZ_NAME` = `Asia/Kolkata`
   - `CRON_KEY` = another random text (for Step 5)
   - `DEV_LOGIN` = `1` **only for a demo.** This lets anyone use any registration number. For the real library you need the college sign-in instead.
4. Click **Create Web Service**. When it says Live, open `https://YOUR-NAME.onrender.com/healthz`. You should see `{"ok": true}`.

## Step 4. Put the website online
1. In Render click **New > Static Site**, pick the same repository, set **Root Directory** to `website`. No build command. Publish directory `.`.
2. Before that, open `website/config.js` and change the line to your backend address:
   `window.LIBSEAT_API = "https://YOUR-NAME.onrender.com";`
3. Open the website address Render gives you. Go back to Step 3 and set `ALLOWED_ORIGINS` to that address.

## Step 5. Cancel unclaimed seats automatically
The seats are also cancelled whenever someone uses the app, but for exactness make a free scheduler (for example cron-job.org) call your backend every minute:
- Address: `https://YOUR-NAME.onrender.com/api/internal/release`
- Method: POST
- Header: `X-Cron-Key` = your `CRON_KEY`

Calling it every few minutes also stops a free Render service from falling asleep for long. Check the scheduler's own free limits.

## Step 6. Print the QR codes (last!)
1. Open your real website, log in as `admin`, open **QR code generator**.
2. Type your real website address in the box ("Web address students will open") and press Enter.
3. Print the codes. Never print them with a `localhost` address.

## Test it online
1. Log in as a student and book a seat.
2. Open a private window, log in with another number: the seat must show as booked.
3. Scan a QR code with your phone camera.

## What each part costs
Free plans are fine for testing. For the real library with thousands of students use paid plans (always-on servers and a database with backups). See the scaling notes in `README.md`.
