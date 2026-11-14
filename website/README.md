# RYSIL / LibSeat website (your final front-end, connected to the backend)

Files: `index.html` (page), `style.css` (looks), `app.js` (the app), `config.js` (the address of the backend).

## Run it on your computer
1. Keep the backend running (first terminal).
2. Open a SECOND terminal in VS Code (click the + in the terminal panel) and go into this folder (the one that has index.html):
   ```
   python -m http.server 8080
   ```
3. Open http://localhost:8080 in your browser.
4. Log in with a registration number: RA followed by 13 digits (tick the box first).
5. Librarian: type `admin` in the box. A password box appears. Use the ADMIN_PASSWORD you set when starting the backend.

`config.js` has one line, `window.LIBSEAT_API = "http://localhost:5000";`. Change it to the real address of your backend when you put the site online (use "" if the backend is on the same address).

## Late at night
The library is closed at night, so booking is refused. Start the backend with a pretend time (PowerShell):
`$env:FAKE_NOW="2026-10-31T10:00"; $env:DEV_LOGIN="1"; $env:ADMIN_PASSWORD="mypassword"; python app.py`

## What is saved on the server now
Bookings, check-ins, blocked seats, opening hours, and the whole library layout (tables, chairs, zones, signs, room size, floor names, picture). Your layout editor saves by itself a moment after every change.

## QR codes
Admin > QR code generator makes one signed code per seat. The codes open the web address shown in that card, so set it to your real site before printing. `localhost` only works on your own computer.

## Putting it online
See `DEPLOY.md` in the backend folder. After you deploy the backend, change the line in `config.js` to its address.

## Using it on a phone
See `GUIDE.md` in the main folder (Part 1). On a phone, `window.LIBSEAT_API` in `config.js` must be your computer's address (for example `http://192.168.1.23:5000`), because `localhost` on a phone means the phone itself.

## If it says "Can't reach the LibSeat server"
That message means the page works but the backend is not answering (your Wi-Fi is fine). Check: (1) the backend terminal is still running, and its last lines show no error; (2) `http://localhost:5000/healthz` shows `{"ok": true}`; (3) `config.js` has the same address and port; (4) you opened the site from `http://localhost:8080`, not by double-clicking `index.html`; (5) if `.env` has `APP_ENV=production`, `SECRET_KEY` must be set too.
