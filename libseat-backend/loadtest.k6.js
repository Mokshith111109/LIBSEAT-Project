// Install k6 (https://k6.io) and run:  k6 run -e BASE=https://seats.example.edu loadtest.k6.js
// Needs DEV_LOGIN=1 on a TEST server only. Not run here; adjust the stages for your hardware.
import http from 'k6/http'; import { check, sleep } from 'k6';
export const options = { stages: [{ duration: '1m', target: 2000 }, { duration: '3m', target: 5000 }, { duration: '1m', target: 0 }],
  thresholds: { http_req_failed: ['rate<0.01'], http_req_duration: ['p(95)<800'] } };
const J = { headers: { 'Content-Type': 'application/json' } };
export default function () {
  const reg = 'RA' + String(Math.floor(Math.random() * 1e13)).padStart(13, '0');
  const l = http.post(`${__ENV.BASE}/api/login`, JSON.stringify({ reg_no: reg }), J);
  const auth = { headers: { Authorization: 'Bearer ' + l.json('token'), 'Content-Type': 'application/json' } };
  check(http.get(`${__ENV.BASE}/api/availability?floor=G&start=600&end=660`, auth), { ok: (r) => r.status === 200 });
  const seat = 'G' + (1 + Math.floor(Math.random() * 32));
  const b = http.post(`${__ENV.BASE}/api/bookings`, JSON.stringify({ seat, start: 600, end: 660 }), auth);
  check(b, { 'booked or clean conflict': (r) => r.status === 201 || r.status === 409 });
  sleep(1);
}
