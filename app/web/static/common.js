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

// ---------- WHOOP recovery bands ----------
// red 0-33, yellow 34-66, green 67-100 (CSS classes rec-red / rec-yellow / rec-green, colors in style.css)
function recBand(score) {
  return score >= 67 ? "rec-green" : score >= 34 ? "rec-yellow" : "rec-red";
}
function recColor(score) {
  return getComputedStyle(document.documentElement).getPropertyValue(`--${recBand(score)}`).trim();
}

// ---------- top nav + theme toggle ----------
// Call mountNav("runs" | "training" | "analytics" | "home") at the start of a page's script.
// Changing the theme fires a "themechange" event so pages can redraw charts in the new colors.
function currentTheme() {
  try { return localStorage.getItem("theme") || "auto"; } catch (e) { return "auto"; }
}
function setTheme(t) {
  try { t === "auto" ? localStorage.removeItem("theme") : localStorage.setItem("theme", t); } catch (e) { /* ignore */ }
  if (t === "auto") document.documentElement.removeAttribute("data-theme");
  else document.documentElement.setAttribute("data-theme", t);
  document.querySelectorAll(".theme-toggle button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.t === t)));
  window.dispatchEvent(new Event("themechange"));
}
function mountNav(active) {
  const links = [["runs", "/runs", "Runs"], ["training", "/training", "Training"], ["analytics", "/analytics", "Analytics"]];
  const now = currentTheme();
  const nav = el("header", { class: "topnav" }, el("div", { class: "topnav-inner" },
    el("a", { class: "brand", href: "/" }, "Training Platform"),
    el("nav", { class: "navlinks", "aria-label": "Sections" },
      links.map(([key, href, label]) => {
        const a = el("a", { href }, label);
        if (key === active) a.setAttribute("aria-current", "page");
        return a;
      })),
    el("div", { class: "theme-toggle", role: "group", "aria-label": "Theme" },
      [["auto", "Auto"], ["light", "Light"], ["dark", "Dark"]].map(([t, label]) =>
        el("button", { type: "button", "data-t": t, "aria-pressed": String(t === now), onclick: () => setTheme(t),
                       title: t === "auto" ? "Follow the system setting" : `Always ${label.toLowerCase()}` }, label)))));
  document.body.prepend(nav);
}

// ---------- ring gauge (WHOOP-style) ----------
// ring({ value, max, color, text, unit, label, sub, size }) -> element. value null draws an empty track.
function ring({ value, max, color, text, unit = "", label = "", sub = "", size = 128 }) {
  const NS = "http://www.w3.org/2000/svg";
  const s = (tag, attrs, txt) => {
    const n = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
    if (txt != null) n.textContent = txt;
    return n;
  };
  const stroke = Math.round(size * 0.09), r = (size - stroke) / 2, c = 2 * Math.PI * r;
  const frac = value == null ? 0 : Math.max(0, Math.min(1, value / max));
  const svg = s("svg", { width: size, height: size, viewBox: `0 0 ${size} ${size}`, role: "img",
                         "aria-label": `${label}: ${value == null ? "no data" : text + unit}` });
  svg.append(
    s("circle", { cx: size / 2, cy: size / 2, r, fill: "none", stroke: "var(--line)", "stroke-width": stroke }),
    s("circle", { cx: size / 2, cy: size / 2, r, fill: "none", stroke: color, "stroke-width": stroke, "stroke-linecap": "round",
                  "stroke-dasharray": `${(frac * c).toFixed(1)} ${c.toFixed(1)}`, transform: `rotate(-90 ${size / 2} ${size / 2})` }),
    s("text", { x: size / 2, y: size / 2 + size * 0.06, "text-anchor": "middle", class: "ring-value",
                "font-size": Math.round(size * 0.26) }, value == null ? "–" : text),
  );
  if (unit && value != null) {
    svg.append(s("text", { x: size / 2, y: size / 2 + size * 0.24, "text-anchor": "middle", class: "ring-unit",
                           "font-size": Math.round(size * 0.1) }, unit));
  }
  return el("div", { class: "ring" }, svg, label ? el("div", { class: "ring-label" }, label) : "",
            sub ? el("div", { class: "ring-sub" }, sub) : "");
}
function recoveryRing(score, opts = {}) {
  return ring({ value: score, max: 100, color: score == null ? "var(--line)" : `var(--${recBand(score)}-ring)`,
                text: score == null ? "–" : `${Math.round(score)}`, unit: "%", label: "Recovery", ...opts });
}
function strainRing(strain, opts = {}) {
  return ring({ value: strain, max: 21, color: "var(--strain)", text: strain == null ? "–" : strain.toFixed(1),
                label: "Strain", ...opts });
}
