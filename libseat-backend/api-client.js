// Drop this into your front-end (before app.js). It replaces the browser-storage logic with server calls.
// Set API_BASE to where the backend runs, e.g. 'https://seats.example.edu' (or '' if it is on the same site).
const API_BASE = '';
let TOKEN = sessionStorage.getItem('ls_token') || null;

async function api(path, method = 'GET', body) {
  let r;
  try {
    r = await fetch(API_BASE + path, {
      method, headers: { 'Content-Type': 'application/json', ...(TOKEN ? { Authorization: 'Bearer ' + TOKEN } : {}) },
      body: body ? JSON.stringify(body) : undefined });
  } catch (e) { throw { error: 'offline', message: 'You are offline. Check your connection.' }; }   // -> show the "You are offline" page
  const data = await r.json().catch(() => ({}));
  if (!r.ok) {
    if (r.status === 401) { TOKEN = null; sessionStorage.removeItem('ls_token'); }                  // -> go back to login
    throw { status: r.status, ...data };                                                              // data.error is a code, data.message is text
  }
  return data;
}
const Api = {
  async login(reg)        { const d = await api('/api/login', 'POST', { reg_no: reg }); TOKEN = d.token; sessionStorage.setItem('ls_token', TOKEN); return d; },
  async adminLogin(u, p)  { const d = await api('/api/admin/login', 'POST', { username: u, password: p }); TOKEN = d.token; sessionStorage.setItem('ls_token', TOKEN); return d; },
  config:       ()        => api('/api/config'),                                   // opening hours, server time
  layout:       ()        => api('/api/layout'),                                   // seats + tables for both floors
  availability: (f, s, e) => api(`/api/availability?floor=${f}&start=${s}&end=${e}`),   // [{id,status: free|booked|mine|blocked}]
  book:         (seat, s, e) => api('/api/bookings', 'POST', { seat, start: s, end: e }),
  myBookings:   ()        => api('/api/bookings/me'),
  cancel:       (id)      => api('/api/bookings/' + id, 'DELETE'),
  checkin:      (seat, token) => api('/api/checkin', 'POST', { seat, token }),         // result: ok|done|early|rel|other|none
  // admin
  saveLayout:   (f, L)    => api('/api/admin/layout/' + f, 'PUT', { seats: L.seats, tables: L.tables, img: L.img, decor: L.decor }),
  block:        (seat, on) => api(`/api/admin/seats/${seat}/blocked`, 'PUT', { blocked: on }),
  setHours:     (o, c)    => api('/api/admin/settings', 'PUT', { open: o, close: c }),
  stats:        (f)       => api('/api/admin/stats?floor=' + f),
  qrCodes:      (f, base) => api(`/api/admin/qr?floor=${f}&base=${encodeURIComponent(base)}`),   // each code has the signed link to print
};
// QR links now look like  https://your-site/#checkin=G5.<token>  -> read both parts:
//   const m = /checkin=([GF]\d{1,3})\.([a-f0-9]{16})/.exec(location.hash);  Api.checkin(m[1], m[2]);
// Error codes to show on your existing screens/dialogs:
//   already_reserved -> "You already have a reserved seat at this time"   seat_taken -> "Seat already booked"
//   limit_reached -> "Limit: 2 bookings per day"   closed / time_passed -> the "Booking closed" / "Time passed" pages
//   too_many_requests (429) -> "Too busy, try again in a moment"          offline -> the "You are offline" page
