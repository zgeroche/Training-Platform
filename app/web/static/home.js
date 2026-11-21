// Home dashboard: today's recovery/strain/sleep, this week's plan vs actual, next run, race countdown.
(async function () {
  const box = document.getElementById("dash");
  if (!box) return;
  const LENGTH = { short: "Short", medium: "Medium", long: "Long" };
  const EFFORT = { easy: "Easy", race_pace: "Race pace", tempo: "Tempo" };
  const planText = (p) => p.kind === "race" ? `${p.note || "Race"} · ${p.distance_mi} mi`
    : [LENGTH[p.length], EFFORT[p.effort], p.distance_mi ? `${p.distance_mi.toFixed(1)} mi` : null].filter(Boolean).join(" · ") || "Run";
  const dayName = (iso, opts) => new Date(iso + "T12:00:00Z").toLocaleDateString(undefined, { timeZone: "UTC", ...opts });
  const hm = (h) => (h == null ? "–" : `${Math.floor(h)}h ${String(Math.round((h % 1) * 60)).padStart(2, "0")}m`);

  try {
    const cal = await getJSON("/api/calendar");
    const today = cal.today;
    let day = null;
    try { day = await getJSON(`/api/days/${today}`); } catch (e) { /* no WHOOP cycle yet today */ }

    // Next planned run from today on (looking into next month if needed), skipping one already done today.
    const isPlannedRun = (d) => d.date >= today && d.plan && (d.plan.kind === "run" || d.plan.kind === "race") && d.status !== "done";
    let next = cal.weeks.flatMap((w) => w.days).find(isPlannedRun);
    if (!next) {
      const ym = cal.month === 12 ? `${cal.year + 1}-01` : `${cal.year}-${String(cal.month + 1).padStart(2, "0")}`;
      try { next = (await getJSON(`/api/calendar?month=${ym}`)).weeks.flatMap((w) => w.days).find(isPlannedRun); } catch (e) { /* ignore */ }
    }
    const week = cal.weeks.find((w) => w.days.some((d) => d.date === today));

    // Today
    const todayCard = el("section", { class: "panel dash-card" },
      el("h2", {}, `Today · ${dayName(today, { weekday: "long", month: "short", day: "numeric" })}`));
    if (day) {
      const s = day.sleep;
      todayCard.append(el("div", { class: "dash-rings" },
        recoveryRing(day.recovery ? day.recovery.score : null, { size: 132 }),
        strainRing(day.day_strain, { size: 132, label: day.in_progress ? "Strain so far" : "Strain" }),
        el("div", { class: "dash-sleep" },
          el("div", { class: "ring-label" }, "Sleep"),
          el("div", { class: "big-num" }, s ? hm(s.hours_asleep) : "–"),
          el("div", { class: "muted" }, s && s.performance_pct != null ? `${Math.round(s.performance_pct)}% performance` : ""),
          el("div", { class: "muted" }, day.recovery && day.recovery.hrv_ms != null
            ? `HRV ${Math.round(day.recovery.hrv_ms)} ms · RHR ${Math.round(day.recovery.resting_heart_rate)}` : ""))));
    } else {
      todayCard.append(el("p", { class: "muted" }, "No WHOOP data for today yet."));
    }

    // This week
    const weekCard = el("section", { class: "panel dash-card" }, el("h2", {}, "This week"));
    if (week) {
      const pct = week.planned_miles ? Math.min(100, (100 * week.run_miles) / week.planned_miles) : null;
      weekCard.append(
        el("div", { class: "dash-miles" }, el("span", { class: "big-num" }, week.run_miles.toFixed(1)),
          el("span", { class: "muted" }, week.planned_miles ? ` of ${week.planned_miles.toFixed(1)} mi planned` : " mi")),
        pct != null ? el("div", { class: "dash-bar", role: "img", "aria-label": `${Math.round(pct)}% of planned miles` },
          el("div", { style: `width:${pct.toFixed(0)}%` })) : "",
        el("div", { class: "muted" }, [
          week.done ? `${week.done} done` : null, week.short ? `${week.short} short` : null,
          week.missed ? `${week.missed} missed` : null, week.extra ? `${week.extra} extra` : null,
        ].filter(Boolean).join(" · ") || `${week.planned_runs} planned run${week.planned_runs === 1 ? "" : "s"}`));
    }
    weekCard.append(el("div", { class: "dash-next" },
      el("div", { class: "ring-label" }, "Next run"),
      next ? el("a", { href: `/training#${next.date.slice(0, 7)}` },
        el("b", {}, next.date === today ? "Today" : dayName(next.date, { weekday: "long", month: "short", day: "numeric" })),
        ` · ${planText(next.plan)}`)
        : el("span", { class: "muted" }, "Nothing planned yet.")));

    // Race
    const race = cal.race;
    const n = race.days_to_go;
    const raceCard = el("section", { class: "panel dash-card dash-race" },
      el("h2", {}, race.name),
      el("div", {}, el("span", { class: "big-num" }, n > 0 ? n : n === 0 ? "Today" : "Done"),
        n > 0 ? el("span", { class: "muted" }, ` days to go`) : ""),
      el("div", { class: "muted" }, n > 0 ? `${Math.floor(n / 7)} weeks ${n % 7} days` : ""),
      el("div", {}, dayName(race.date, { weekday: "long", month: "long", day: "numeric", year: "numeric" }),
        el("span", { class: "muted" }, ` · ${race.distance_mi} mi`)));

    box.replaceChildren(todayCard, weekCard, raceCard);
  } catch (err) {
    box.replaceChildren(el("p", { class: "muted" }, `Couldn't load the dashboard: ${err.message}`));
  }
})();
