// Shared formatting for run pages. All API datetimes are naive UTC.
const MILE_M = 1609.344;
const FT_PER_M = 3.28084;

function parseOffsetMinutes(offset) {
  // "UTC-0700" -> -420
  const m = /UTC([+-])(\d{2})(\d{2})/.exec(offset || "");
  if (!m) return 0;
  const mins = Number(m[2]) * 60 + Number(m[3]);
  return m[1] === "-" ? -mins : mins;
}

// Shift a UTC timestamp into the run's own time zone; read the result with getUTC* / timeZone "UTC".
function localDate(isoUtc, offset) {
  const d = new Date(isoUtc.endsWith("Z") ? isoUtc : isoUtc + "Z");
  return new Date(d.getTime() + parseOffsetMinutes(offset) * 60000);
}

function fmtDate(isoUtc, offset, opts) {
  return localDate(isoUtc, offset).toLocaleString(undefined, { timeZone: "UTC", ...opts });
}

function fmtDuration(ms) {
  if (ms == null) return "–";
  const s = Math.round(ms / 1000);
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  const pad = (n) => String(n).padStart(2, "0");
  return h ? `${h}:${pad(m)}:${pad(sec)}` : `${m}:${pad(sec)}`;
}

function fmtPace(secPerMile) {
  if (!secPerMile || !isFinite(secPerMile)) return "–";
  const s = Math.round(secPerMile);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

function fmtNum(v, digits = 0) {
  return v == null ? "–" : Number(v).toFixed(digits);
}

async function getJSON(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${url} returned ${res.status}`);
  return res.json();
}

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v);
  }
  for (const c of children.flat()) node.append(c instanceof Node ? c : String(c ?? ""));
  return node;
}
