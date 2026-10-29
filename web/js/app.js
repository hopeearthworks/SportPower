/* Sports Monitor — front end.
 * Talks to main.py (/api/*), which uses sports.py and football_monitor.py.
 */
(() => {
    "use strict";

    const AUTO_REFRESH_SECONDS = 60;
    const BOUTS_SHOWN = 6;
    const RESULTS_SHOWN = 10;

    // Per-sport labels and the fifth "highlight" stat tile
    const sumScores = (events) => events
        .filter((e) => e.kind === "match" && e.state !== "pre")
        .reduce((sum, e) => sum + (parseInt(e.home.score, 10) || 0) + (parseInt(e.away.score, 10) || 0), 0);

    const SPORT_UI = {
        football: { total: "Matches", one: "match", results: "Matches", extra: ["Goals", "bi-bullseye", sumScores] },
        rugby: { total: "Matches", one: "match", results: "Matches", extra: ["Points", "bi-plus-slash-minus", sumScores] },
        f1: {
            total: "Grands Prix", one: "Grand Prix", results: "Race weekends",
            extra: ["Sessions done", "bi-stopwatch", (ev) => ev.reduce((n, e) => n + e.sessions.filter((s) => s.completed).length, 0)],
        },
        cricket: {
            total: "Matches", one: "match", results: "Matches",
            extra: ["Competitions", "bi-trophy", (ev) => new Set(ev.map((e) => e.competition)).size],
        },
        combat: {
            total: "Fight cards", one: "fight card", results: "Fight cards",
            extra: ["Bouts", "bi-people", (ev) => ev.reduce((n, e) => n + e.bouts.length, 0)],
        },
    };

    const state = {
        sports: {},
        sport: "football",
        leagueBySport: {},
        league: "sco.1",
        date: "",          // YYYYMMDD, "" = ESPN's current events
        search: "",
        status: "all",     // all | live | completed | scheduled
        data: null,
        f1Session: {},     // event id -> chosen session code
        expanded: new Set(),
        logLevel: "ALL",
        logLines: [],
        report: null,
        requestId: 0,
        countdown: AUTO_REFRESH_SECONDS,
        timer: null,
        oracle: { sport: "", league: "", loaded: false, requestId: 0, data: null },
        match: { fixture: null, sport: "", league: "", leagueName: "", detail: null, odds: null, view: "detail", requestId: 0 },
    };

    const $ = (id) => document.getElementById(id);
    const el = {
        sportTabs: $("sportTabs"), sportNote: $("sportNote"),
        leagueSelect: $("leagueSelect"), customLeague: $("customLeague"),
        dateInput: $("dateInput"), prevDay: $("prevDay"), nextDay: $("nextDay"), matchdayBtn: $("matchdayBtn"),
        teamInput: $("teamInput"), teamList: $("teamList"), clearTeam: $("clearTeam"), searchLabel: $("searchLabel"),
        statusFilter: $("statusFilter"),
        matches: $("matchesContainer"), resultsTabLabel: $("resultsTabLabel"),
        leagueLogo: $("leagueLogo"), leagueLogoFallback: $("leagueLogoFallback"),
        leagueName: $("leagueName"), leagueSeason: $("leagueSeason"), leagueSub: $("leagueSub"),
        statTotal: $("statTotal"), statTotalLabel: $("statTotalLabel"), statLive: $("statLive"), statDone: $("statDone"),
        statUpcoming: $("statUpcoming"), statExtra: $("statExtra"), statExtraLabel: $("statExtraLabel"), statExtraIcon: $("statExtraIcon"),
        lastUpdated: $("lastUpdated"), refreshDot: $("refreshDot"), refreshBtn: $("refreshBtn"),
        autoRefresh: $("autoRefresh"), countdown: $("countdown"), themeSwitch: $("themeSwitch"),
        outputFile: $("outputFile"), settingsSummary: $("settingsSummary"),
        generateBtn: $("generateBtn"), saveConfigBtn: $("saveConfigBtn"),
        reportView: $("reportView"), reportFileName: $("reportFileName"),
        copyReport: $("copyReport"), downloadReport: $("downloadReport"),
        logView: $("logView"), logFilter: $("logFilter"), refreshLog: $("refreshLog"), logTabBtn: $("logTabBtn"),
        toasts: $("toastContainer"),
        oracle: $("oracle"), oracleSports: $("oracleSports"), oracleLeague: $("oracleLeague"),
        oracleList: $("oracleList"), oracleRefresh: $("oracleRefresh"),
        matchModal: $("matchModal"), mmEyebrow: $("mmEyebrow"), mmTitle: $("matchModalTitle"),
        mmBody: $("mmBody"), mmFootNote: $("mmFootNote"), betBtn: $("betBtn"), betBack: $("betBack"),
    };

    /* ---------------- helpers ---------------- */

    const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({
        "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    })[c]);

    const toInputDate = (yyyymmdd) => yyyymmdd ? `${yyyymmdd.slice(0, 4)}-${yyyymmdd.slice(4, 6)}-${yyyymmdd.slice(6, 8)}` : "";
    const fromInputDate = (value) => value.replaceAll("-", "");
    const toYMD = (d) => `${d.getFullYear()}${String(d.getMonth() + 1).padStart(2, "0")}${String(d.getDate()).padStart(2, "0")}`;
    const parseYMD = (s) => new Date(+s.slice(0, 4), +s.slice(4, 6) - 1, +s.slice(6, 8));

    const fmtTime = (d) => d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    const fmtDay = (d) => d.toLocaleDateString([], { weekday: "long", day: "numeric", month: "long", year: "numeric" });
    const fmtShortDay = (d) => d.toLocaleDateString([], { weekday: "short", day: "numeric", month: "short" });

    const sportInfo = () => state.sports[state.sport] || { name: "", emoji: "🏆", leagues: [], search_label: "Team" };
    const sportUI = () => SPORT_UI[state.sport] || SPORT_UI.football;

    function relativeTime(date) {
        const mins = Math.round((date - Date.now()) / 60000);
        if (mins <= 0) return "Due to start";
        if (mins < 60) return `in ${mins}m`;
        const hours = Math.floor(mins / 60);
        if (hours < 24) return `in ${hours}h ${mins % 60}m`;
        const days = Math.floor(hours / 24);
        return `in ${days}d ${hours % 24}h`;
    }

    function debounce(fn, ms) {
        let t;
        return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
    }

    async function api(path, options = {}) {
        const response = await fetch(path, {
            ...options,
            headers: { "Content-Type": "application/json", ...(options.headers || {}) },
        });
        let data = {};
        try { data = await response.json(); } catch (e) { /* non-JSON error */ }
        if (!response.ok) throw new Error(data.error || `Request failed (${response.status})`);
        return data;
    }

    function toast(message, type = "success") {
        const icon = type === "success" ? "bi-check-circle-fill" : "bi-exclamation-triangle-fill";
        const node = document.createElement("div");
        node.className = "toast";
        node.setAttribute("role", "status");
        node.innerHTML = `<div class="toast-body"><i class="bi ${icon}"></i><span>${esc(message)}</span>
            <button type="button" class="btn-close ms-auto" data-bs-dismiss="toast" aria-label="Close"></button></div>`;
        el.toasts.appendChild(node);
        const t = new bootstrap.Toast(node, { delay: 3500 });
        node.addEventListener("hidden.bs.toast", () => node.remove());
        t.show();
    }

    // Mirrors matches_status() in sports.py so status tabs switch instantly
    function matchesStatus(item, status) {
        if (status === "completed") return item.completed;
        if (status === "scheduled") return item.state === "pre";
        if (status === "live") return item.state === "in";
        return true;
    }

    const isHit = (name) => !!state.search && name.toLowerCase().includes(state.search.toLowerCase());

    function highlight(name) {
        const query = state.search.toLowerCase();
        const at = query ? name.toLowerCase().indexOf(query) : -1;
        if (at < 0) return esc(name);
        return esc(name.slice(0, at)) + `<mark>${esc(name.slice(at, at + query.length))}</mark>` + esc(name.slice(at + query.length));
    }

    const flagImg = (src, cls = "flag") => src ? `<img class="${cls}" src="${esc(src)}" alt="" loading="lazy">` : "";

    /* ---------------- data loading ---------------- */

    async function loadEvents({ silent = false } = {}) {
        const id = ++state.requestId;
        setRefreshing(true);
        if (!silent) renderSkeletons();

        const params = new URLSearchParams({ sport: state.sport, league: state.league });
        if (state.search) params.set("search", state.search);
        if (state.date) params.set("date", state.date);

        try {
            const data = await api(`/api/events?${params}`);
            if (id !== state.requestId) return; // a newer request has started
            state.data = data;
            if (state.sport === "cricket") populateLeagues();
            renderAll();
            setUpdated("ok");
        } catch (error) {
            if (id !== state.requestId) return;
            state.data = null;
            renderError(error.message);
            renderStats([]);
            setUpdated("error");
        } finally {
            if (id === state.requestId) setRefreshing(false);
        }
    }

    async function loadLog() {
        el.refreshLog.classList.add("spinning");
        try {
            const { lines } = await api("/api/log");
            state.logLines = lines;
            renderLog();
        } catch (error) {
            el.logView.innerHTML = `<div class="log-line ERROR"><span class="ts"></span><span class="lvl">ERROR</span><span class="msg">${esc(error.message)}</span></div>`;
        } finally {
            el.refreshLog.classList.remove("spinning");
        }
    }

    /* ---------------- rendering: chrome ---------------- */

    function setRefreshing(on) {
        el.refreshBtn.classList.toggle("spinning", on);
        if (on) el.refreshDot.className = "dot loading";
    }

    function setUpdated(status) {
        el.refreshDot.className = `dot ${status}`;
        el.lastUpdated.textContent = status === "ok" ? `Updated ${fmtTime(new Date())}` : "Update failed";
    }

    function renderSportTabs() {
        el.sportTabs.innerHTML = Object.entries(state.sports).map(([key, sport], i) => `
            <button class="sport-tab ${key === state.sport ? "active" : ""}" role="tab" data-sport="${esc(key)}"
                    aria-selected="${key === state.sport}" title="${esc(sport.name)} (${i + 1})">
                <span class="emoji" aria-hidden="true">${sport.emoji}</span>${esc(sport.name)}
            </button>`).join("");
    }

    function populateLeagues() {
        const sport = sportInfo();
        let options = [...sport.leagues];
        if (sport.dynamic_leagues && state.data?.sport === state.sport) {
            const seen = new Set(options.map((o) => o.id));
            options = options.concat(state.data.competitions.filter((c) => !seen.has(c.id)));
        }
        if (!options.some((o) => o.id === state.league) && !sport.custom_league) {
            options.push({ id: state.league, name: state.league });
        }
        el.leagueSelect.innerHTML = options.map((o) => `<option value="${esc(o.id)}">${esc(o.name)}</option>`).join("")
            + (sport.custom_league ? '<option value="custom">Custom code…</option>' : "");
    }

    function renderAll() {
        const { data } = state;
        renderHeader(data);
        renderStats(data.events);
        renderEventList();
        el.teamList.innerHTML = data.names.map((n) => `<option value="${esc(n)}">`).join("");
        renderSettings();
    }

    function renderHeader(data) {
        const sport = sportInfo();
        el.leagueName.textContent = data.name;
        el.leagueSeason.textContent = data.season || sport.name;
        el.leagueLogoFallback.textContent = sport.emoji;
        if (data.logo) {
            el.leagueLogo.src = data.logo;
            el.leagueLogo.classList.remove("d-none");
            el.leagueLogoFallback.classList.add("d-none");
        } else {
            el.leagueLogo.classList.add("d-none");
            el.leagueLogoFallback.classList.remove("d-none");
        }
        const when = state.date ? fmtDay(parseYMD(state.date)) : "Latest";
        const ui = sportUI();
        const label = data.total === 1 ? ui.one : (state.sport === "f1" ? ui.total : ui.total.toLowerCase());
        const filter = state.search ? ` · filtered by “${state.search}”` : "";
        el.leagueSub.textContent = `${when} · ${data.total} ${label} in feed${filter}`;
        document.title = `${data.name} · Sports Monitor`;
    }

    function renderStats(events) {
        const [label, icon, compute] = sportUI().extra;
        el.statTotal.textContent = events.length;
        el.statLive.textContent = events.filter((e) => e.state === "in").length;
        el.statDone.textContent = events.filter((e) => e.completed).length;
        el.statUpcoming.textContent = events.filter((e) => e.state === "pre").length;
        el.statExtra.textContent = events.length ? compute(events) : 0;
        el.statExtraLabel.textContent = label;
        el.statExtraIcon.className = `bi ${icon}`;
        el.statTotalLabel.textContent = sportUI().total;
    }

    function renderSkeletons() {
        const kind = sportInfo().kind;
        const single = kind === "race" || kind === "card";
        el.matches.innerHTML = `<div class="match-grid ${single ? "single" : ""}">${'<div class="skeleton-card"></div>'.repeat(single ? 2 : 4)}</div>`;
    }

    function renderError(message) {
        el.leagueName.textContent = "Unable to load results";
        el.leagueSub.textContent = message;
        el.matches.innerHTML = `
            <div class="empty-state error">
                <div class="icon"><i class="bi bi-wifi-off"></i></div>
                <h3>Something went wrong</h3>
                <p>${esc(message)}</p>
                <button class="btn btn-ghost mt-3" data-action="retry"><i class="bi bi-arrow-clockwise"></i> Try again</button>
            </div>`;
    }

    /* ---------------- rendering: events ---------------- */

    function renderEventList() {
        const events = state.data.events
            .filter((e) => matchesStatus(e, state.status))
            .sort((a, b) => new Date(a.date) - new Date(b.date));
        state.shown = events;

        if (!events.length) {
            const who = sportInfo().search_label.toLowerCase();
            const reason = state.search && !state.data.events.length
                ? `No results for “${state.search}”. Check the spelling — the ${who} list suggests valid names.`
                : "Nothing meets the selected filters. Try another status, date or competition.";
            el.matches.innerHTML = `
                <div class="empty-state">
                    <div class="icon"><i class="bi bi-calendar-x"></i></div>
                    <h3>Nothing to show</h3>
                    <p>${esc(reason)}</p>
                </div>`;
            return;
        }

        const groups = new Map();
        for (const event of events) {
            const day = fmtDay(new Date(event.date));
            if (!groups.has(day)) groups.set(day, []);
            groups.get(day).push(event);
        }

        let index = 0;
        el.matches.innerHTML = [...groups].map(([day, list]) => {
            const single = list[0].kind !== "match";
            return `
                <section class="day-group">
                    <h2 class="day-heading">${esc(day)} <span class="count">${list.length}</span></h2>
                    <div class="match-grid ${single ? "single" : ""}">${list.map((e) => eventCard(e, index++)).join("")}</div>
                </section>`;
        }).join("");
    }

    function eventCard(event, index) {
        if (event.kind === "race") return raceCard(event, index);
        if (event.kind === "card") return fightCard(event, index);
        if (state.sport === "cricket") return cricketCard(event, index);
        return matchCard(event, index);
    }

    function statusPill(item, upcomingLabel = "Upcoming") {
        if (item.state === "in") return `<span class="status-pill live"><span class="live-dot"></span>Live ${esc(item.clock || "")}</span>`;
        if (item.state === "post" && item.completed) return `<span class="status-pill ft"><i class="bi bi-check2"></i>${esc(item.status || "Final")}</span>`;
        if (item.state === "post") return `<span class="status-pill off"><i class="bi bi-exclamation-circle"></i>${esc(item.status)}</span>`;
        return `<span class="status-pill"><i class="bi bi-clock"></i>${esc(upcomingLabel)}</span>`;
    }

    const delay = (index) => `animation-delay:${Math.min(index * 40, 400)}ms`;
    // Results cards open the match modal; index points into state.shown
    const openable = (index) => `data-ev="${index}" role="button" tabindex="0" aria-label="Open match details"`;

    // ----- football & rugby -----

    function teamBlock(side, lost) {
        const abbr = side.abbr || side.name.slice(0, 3).toUpperCase();
        const form = (side.form || "").slice(-5).split("").filter((c) => "WDL".includes(c));
        const logoHtml = side.logo
            ? `<img class="team-logo" src="${esc(side.logo)}" alt="" loading="lazy" data-abbr="${esc(abbr)}">`
            : `<div class="team-logo-fallback">${esc(abbr)}</div>`;
        return `
            <div class="team ${lost ? "lost" : ""}">
                ${logoHtml}
                <div class="team-name" title="${esc(side.name)}">${highlight(side.name)}</div>
                ${form.length ? `<div class="form-dots" title="Form (oldest → latest): ${esc(form.join(" "))}">${form.map((r) => `<span class="${r}"></span>`).join("")}</div>` : ""}
                ${side.record ? `<div class="team-record" title="Season record W-D-L">${esc(side.record)}</div>` : ""}
            </div>`;
    }

    function incidentsBlock(match) {
        if (!match.incidents?.length) return "";
        const scoreIcon = state.sport === "rugby" ? '<span class="tag-try">T</span>' : '<i class="bi bi-record-circle"></i>';
        const item = (i) => {
            const icon = i.type === "red_card" ? '<span class="card-red"></span>' : scoreIcon;
            const tag = /own goal/i.test(i.label) ? " (OG)" : /penalty/i.test(i.label) ? " (P)" : "";
            return `<li title="${esc(i.label)}">${icon} ${esc(i.player)}${tag} <span class="min">${esc(i.minute)}</span></li>`;
        };
        const home = match.incidents.filter((i) => i.side === "home").map(item).join("");
        const away = match.incidents.filter((i) => i.side === "away").map(item).join("");
        return `<footer class="incidents"><ul>${home}</ul><ul class="away-col">${away}</ul></footer>`;
    }

    function matchCard(match, index) {
        const kickoff = new Date(match.date);
        const live = match.state === "in";
        const played = match.state !== "pre" && (match.completed || live);
        const hs = +match.home.score, as = +match.away.score;
        const homeLost = match.completed && hs < as;
        const awayLost = match.completed && as < hs;
        const color = (hex) => (/^[0-9a-f]{6}$/i.test(hex || "") ? `#${hex}` : "");

        const centre = played
            ? `<div class="scoreline">
                   <span class="${homeLost ? "dim" : ""}">${esc(match.home.score)}</span>
                   <span class="sep">–</span>
                   <span class="${awayLost ? "dim" : ""}">${esc(match.away.score)}</span>
               </div>`
            : `<div class="kickoff">
                   <div class="time">${esc(fmtTime(kickoff))}</div>
                   <div class="vs">${match.state === "pre" ? "Kick-off" : "vs"}</div>
                   ${match.state === "pre" ? `<div class="countdown-text">${esc(relativeTime(kickoff))}</div>` : ""}
               </div>`;

        return `
            <article class="match-card ${live ? "is-live" : ""}" ${openable(index)} style="${delay(index)}">
                <div class="match-accent" style="--home-color:${color(match.home.color)};--away-color:${color(match.away.color)}"></div>
                <header class="match-meta">
                    ${statusPill(match)}
                    <span class="venue"><i class="bi bi-geo-alt"></i><span>${esc(match.venue || "Venue TBC")}</span></span>
                </header>
                <div class="match-body">
                    ${teamBlock(match.home, homeLost)}
                    ${centre}
                    ${teamBlock(match.away, awayLost)}
                </div>
                ${incidentsBlock(match)}
            </article>`;
    }

    // ----- cricket -----

    function cricketRow(side, decided) {
        const abbr = side.abbr || side.name.slice(0, 3).toUpperCase();
        const logo = side.logo
            ? `<img class="team-logo" src="${esc(side.logo)}" alt="" loading="lazy" data-abbr="${esc(abbr)}">`
            : `<div class="team-logo-fallback">${esc(abbr)}</div>`;
        // Split "303 & 307/6 (88 ov) (f/o)" into "303 & 307/6" and "88 ov · f/o"
        const score = side.score || "";
        const main = score.replace(/\s*\([^)]*\)/g, "").trim();
        const detail = (score.match(/\(([^)]*)\)/g) || []).map((part) => part.slice(1, -1)).join(" · ");
        const cls = decided ? (side.winner ? "won" : "lost") : "";
        return `
            <div class="cricket-row ${cls}">
                ${logo}
                <div class="cname" title="${esc(side.name)}">${highlight(side.name)}</div>
                <div class="cscore">${esc(main || "–")}${detail ? `<small>${esc(detail)}</small>` : ""}</div>
            </div>`;
    }

    function cricketCard(match, index) {
        const live = match.state === "in";
        const decided = match.completed && (match.home.winner || match.away.winner);
        const start = new Date(match.date);
        const summary = match.summary || (match.state === "pre" ? `Starts ${fmtShortDay(start)}, ${fmtTime(start)} · ${relativeTime(start)}` : "");
        return `
            <article class="match-card ${live ? "is-live" : ""}" ${openable(index)} style="${delay(index)}">
                <header class="match-meta">
                    ${statusPill(match)}
                    <span class="venue"><i class="bi bi-trophy"></i><span class="comp-tag" title="${esc(match.competition)}">${esc(match.competition)}</span></span>
                </header>
                <div class="cricket-body">
                    ${cricketRow(match.home, decided)}
                    ${cricketRow(match.away, decided)}
                </div>
                ${summary ? `<div class="match-summary">${esc(summary)}</div>` : ""}
                ${match.description || match.venue ? `<div class="match-desc"><i class="bi bi-geo-alt"></i> ${esc(match.venue || match.description)}</div>` : ""}
            </article>`;
    }

    // ----- Formula 1 -----

    function pickSession(event) {
        const chosen = state.f1Session[event.id];
        if (chosen && event.sessions.some((s) => s.code === chosen)) return event.sessions.find((s) => s.code === chosen);
        const live = event.sessions.find((s) => s.state === "in");
        if (live) return live;
        const race = event.sessions.find((s) => s.code === "Race" && s.results.length);
        if (race) return race;
        const done = event.sessions.filter((s) => s.results.length);
        if (done.length) return done[done.length - 1];
        return event.sessions.find((s) => s.state === "pre") || event.sessions[event.sessions.length - 1];
    }

    function sessionBody(event, session) {
        if (!session) return '<div class="schedule-note"><i class="bi bi-calendar-event"></i><div class="big">Schedule TBC</div></div>';
        if (!session.results.length) {
            const start = new Date(session.date);
            const lead = session.state === "in" ? "In progress" : `${fmtShortDay(start)}, ${fmtTime(start)}`;
            return `
                <div class="schedule-note">
                    <i class="bi bi-flag"></i>
                    <div>
                        <div class="big">${esc(session.name)} · ${esc(lead)}</div>
                        <div class="small">${session.state === "pre" ? esc(relativeTime(start)) : esc(session.status)}</div>
                    </div>
                </div>`;
        }

        const podiumOrder = [session.results[1], session.results[0], session.results[2]].filter(Boolean);
        const step = (r) => `
            <div class="podium-step p${r.position} ${isHit(r.name) ? "hit" : ""}">
                <div class="pos">${r.position}</div>
                ${flagImg(r.flag)}
                <div class="driver" title="${esc(r.name)}">${highlight(r.name)}</div>
                <div class="country">${esc(r.country)}</div>
            </div>`;

        const key = `${event.id}:${session.code}`;
        const rest = session.results.slice(3);
        const shown = state.expanded.has(key) ? rest : rest.slice(0, RESULTS_SHOWN - 3);
        const list = shown.map((r) => `
            <li class="${isHit(r.name) ? "hit" : ""}">
                <span class="p">${r.position}</span>${flagImg(r.flag)}<span class="n" title="${esc(r.country)}">${highlight(r.name)}</span>
            </li>`).join("");
        const more = rest.length > shown.length || state.expanded.has(key)
            ? `<button class="more-btn" data-action="expand" data-key="${esc(key)}">${state.expanded.has(key) ? "Show top 10" : `Show all ${session.results.length} drivers`}</button>`
            : "";

        return `
            <div class="podium">${podiumOrder.map(step).join("")}</div>
            ${list ? `<ol class="result-list">${list}</ol>` : ""}
            ${more}`;
    }

    function raceCard(event, index) {
        const session = pickSession(event);
        const start = new Date(event.date);
        const end = event.end_date ? new Date(event.end_date) : null;
        const range = end && end.toDateString() !== start.toDateString()
            ? `${fmtShortDay(start)} – ${fmtShortDay(end)}` : fmtShortDay(start);

        const chips = event.sessions.map((s) => {
            const d = new Date(s.date);
            const dot = s.state === "in" ? "live" : s.completed ? "done" : "";
            return `
                <button class="session-tab ${s === session ? "active" : ""}" data-action="session" data-event="${esc(event.id)}" data-code="${esc(s.code)}">
                    <strong><span class="sdot ${dot}"></span>${esc(s.name)}</strong>
                    ${esc(d.toLocaleDateString([], { weekday: "short" }))} ${esc(fmtTime(d))}
                </button>`;
        }).join("");

        return `
            <article class="event-card ${event.state === "in" ? "is-live" : ""}" ${openable(index)} style="${delay(index)}">
                <header class="event-head">
                    <div class="event-icon">🏁</div>
                    <div class="flex-grow-1 min-w-0">
                        <h3 class="event-title">${esc(event.name)}</h3>
                        <div class="event-sub">
                            <span><i class="bi bi-geo-alt"></i>${esc(event.venue)}${event.location ? `, ${esc(event.location)}` : ""}</span>
                            <span><i class="bi bi-calendar3"></i>${esc(range)}</span>
                        </div>
                    </div>
                    ${statusPill(event, "Upcoming")}
                </header>
                <div class="session-tabs" role="tablist">${chips}</div>
                <div class="session-body">${sessionBody(event, session)}</div>
            </article>`;
    }

    // ----- combat sports -----

    function fighterBlock(f, bout, right) {
        const decided = bout.completed && bout.fighters.some((x) => x.winner);
        const cls = [right ? "right" : "", decided ? (f.winner ? "won" : "lost") : "", isHit(f.name) ? "hit" : ""].join(" ");
        return `
            <div class="fighter ${cls}">
                ${flagImg(f.flag)}
                <div class="min-w-0">
                    <div class="fname" title="${esc(f.name)}${f.country ? ` · ${esc(f.country)}` : ""}">
                        ${highlight(f.name)}${f.winner ? ' <i class="bi bi-check-circle-fill"></i>' : ""}
                    </div>
                    <div class="frec">${esc(f.record || "")}</div>
                </div>
            </div>`;
    }

    function boutResult(bout) {
        if (bout.state === "in") return `<div class="bout-result live">Live · R${esc(bout.round)}</div>`;
        if (bout.completed) {
            const winner = bout.fighters.some((f) => f.winner);
            return `<div class="bout-result">${winner ? `R${esc(bout.round)} · ${esc(bout.clock)}` : esc(bout.status)}</div>`;
        }
        if (bout.state === "post") return `<div class="bout-result">${esc(bout.status)}</div>`;
        const d = new Date(bout.date);
        return `<div class="bout-result">${esc(fmtTime(d))}${bout.scheduled_rounds ? ` · ${esc(bout.scheduled_rounds)} rds` : ""}</div>`;
    }

    function fightCard(event, index) {
        const key = `card:${event.id}`;
        const all = state.expanded.has(key) || !!state.search;
        const bouts = all ? event.bouts : event.bouts.slice(0, BOUTS_SHOWN);
        const rows = bouts.map((b, i) => {
            const [a, z] = [b.fighters[0] || { name: "TBC" }, b.fighters[1] || { name: "TBC" }];
            const main = i === 0 && !state.search;
            return `
                <li class="bout ${main ? "main-event" : ""}">
                    ${fighterBlock(a, b, false)}
                    <div class="bout-mid">
                        <span class="weight-tag">${main ? "Main event" : esc(b.weight_class || "Bout")}</span>
                        ${boutResult(b)}
                    </div>
                    ${fighterBlock(z, b, true)}
                </li>`;
        }).join("");
        const more = event.bouts.length > BOUTS_SHOWN && !state.search
            ? `<button class="more-btn mb-3" data-action="expand" data-key="${esc(key)}">${state.expanded.has(key) ? "Show fewer bouts" : `Show all ${event.bouts.length} bouts`}</button>`
            : "";

        return `
            <article class="event-card ${event.state === "in" ? "is-live" : ""}" ${openable(index)} style="${delay(index)}">
                <header class="event-head">
                    <div class="event-icon">🥊</div>
                    <div class="flex-grow-1 min-w-0">
                        <h3 class="event-title">${esc(event.name)}</h3>
                        <div class="event-sub">
                            <span><i class="bi bi-geo-alt"></i>${esc(event.venue || "Venue TBC")}${event.location ? `, ${esc(event.location)}` : ""}</span>
                            <span><i class="bi bi-clock"></i>${esc(fmtShortDay(new Date(event.date)))}, ${esc(fmtTime(new Date(event.date)))}</span>
                            <span><i class="bi bi-people"></i>${event.bouts.length} bout${event.bouts.length === 1 ? "" : "s"}</span>
                        </div>
                    </div>
                    ${statusPill(event)}
                </header>
                <ul class="bout-list">${rows}</ul>
                ${more}
            </article>`;
    }

    /* ---------------- rendering: report & log ---------------- */

    function renderSettings() {
        const statusLabels = { all: "All", live: "Live", completed: "Completed", scheduled: "Scheduled" };
        const league = el.leagueSelect.selectedOptions[0]?.textContent;
        const rows = [
            ["Sport", sportInfo().name],
            ["Competition", league && el.leagueSelect.value !== "custom" ? league : state.league],
            ["Date", state.date ? toInputDate(state.date) : "Latest"],
            [sportInfo().search_label, state.search || "All"],
            ["Status", statusLabels[state.status]],
        ];
        el.settingsSummary.innerHTML = rows.map(([k, v]) => `<dt>${esc(k)}</dt><dd title="${esc(v)}">${esc(v)}</dd>`).join("");
    }

    function renderLog() {
        const pattern = /^(\S+ \S+?)(?:,\d+)? - (\w+) - (.*)$/;
        const rows = state.logLines
            .map((line) => {
                const m = line.match(pattern);
                return m ? { ts: m[1], level: m[2], msg: m[3] } : { ts: "", level: "INFO", msg: line };
            })
            .filter((r) => state.logLevel === "ALL" || r.level === state.logLevel)
            .reverse();

        el.logView.innerHTML = rows.length
            ? rows.map((r) => `<div class="log-line ${esc(r.level)}"><span class="ts">${esc(r.ts)}</span><span class="lvl">${esc(r.level)}</span><span class="msg">${esc(r.msg)}</span></div>`).join("")
            : `<div class="text-center py-5 text-muted-2">No ${state.logLevel === "ALL" ? "" : state.logLevel.toLowerCase() + " "}entries</div>`;
    }

    function syncControls() {
        const sport = sportInfo();
        el.sportTabs.querySelectorAll(".sport-tab").forEach((b) => {
            const active = b.dataset.sport === state.sport;
            b.classList.toggle("active", active);
            b.setAttribute("aria-selected", String(active));
        });

        const known = [...el.leagueSelect.options].some((o) => o.value === state.league);
        el.leagueSelect.value = known ? state.league : "custom";
        const custom = !known && sport.custom_league;
        el.customLeague.classList.toggle("d-none", !custom);
        if (custom) el.customLeague.value = state.league;

        el.dateInput.value = toInputDate(state.date);
        el.matchdayBtn.classList.toggle("active", !state.date);

        el.searchLabel.textContent = sport.search_label;
        el.teamInput.placeholder = `All ${sport.search_label.toLowerCase()}s`;
        el.teamInput.value = state.search;
        el.clearTeam.classList.toggle("d-none", !state.search);

        el.statusFilter.querySelectorAll("button").forEach((b) => b.classList.toggle("active", b.dataset.status === state.status));
        el.resultsTabLabel.textContent = sportUI().results;

        const note = el.sportNote;
        note.classList.toggle("d-none", !sport.note);
        note.querySelector("span").textContent = sport.note || "";
        renderSettings();
    }

    /* ---------------- actions ---------------- */

    function currentSettings() {
        return {
            sport: state.sport,
            league: state.league,
            team_filter: state.search,
            match_status: state.status,
            date: state.date,
            output_file: el.outputFile.value.trim(),
        };
    }

    function switchSport(sport) {
        if (sport === state.sport || !state.sports[sport]) return;
        state.leagueBySport[state.sport] = state.league;
        state.sport = sport;
        state.league = state.leagueBySport[sport] || state.sports[sport].leagues[0].id;
        state.search = "";
        state.date = "";
        state.data = null;
        state.expanded.clear();
        // Suggest a report name per sport unless the user typed their own
        if (/^\w+_report\.txt$/.test(el.outputFile.value)) el.outputFile.value = `${sport}_report.txt`;
        populateLeagues();
        syncControls();
        loadEvents();
    }

    async function generateReport() {
        el.generateBtn.disabled = true;
        el.generateBtn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> Generating…';
        try {
            const result = await api("/api/report", { method: "POST", body: JSON.stringify(currentSettings()) });
            state.report = result;
            el.reportView.textContent = result.content;
            el.reportView.classList.remove("flash");
            void el.reportView.offsetWidth;
            el.reportView.classList.add("flash");
            el.reportFileName.textContent = result.file;
            el.copyReport.disabled = el.downloadReport.disabled = false;
            toast(`Report saved to ${result.file}`);
        } catch (error) {
            toast(error.message, "error");
        } finally {
            el.generateBtn.disabled = false;
            el.generateBtn.innerHTML = '<i class="bi bi-lightning-charge-fill"></i> Generate report';
        }
    }

    async function saveConfig() {
        try {
            await api("/api/config", { method: "POST", body: JSON.stringify(currentSettings()) });
            toast("Saved to config.json");
        } catch (error) {
            toast(error.message, "error");
        }
    }

    function downloadReport() {
        if (!state.report) return;
        const blob = new Blob([state.report.content], { type: "text/plain" });
        const link = Object.assign(document.createElement("a"), {
            href: URL.createObjectURL(blob),
            download: state.report.file,
        });
        link.click();
        URL.revokeObjectURL(link.href);
    }

    function shiftDay(delta) {
        const base = state.date ? parseYMD(state.date) : new Date();
        base.setDate(base.getDate() + delta);
        state.date = toYMD(base);
        syncControls();
        loadEvents();
    }

    const systemDark = window.matchMedia("(prefers-color-scheme: dark)");

    // pref is "light", "dark" or "system" (follow the operating system setting)
    function setTheme(pref, { animate = true } = {}) {
        const root = document.documentElement;
        const theme = pref === "system" ? (systemDark.matches ? "dark" : "light") : pref;
        if (animate) {
            root.classList.add("theme-transition");
            setTimeout(() => root.classList.remove("theme-transition"), 350);
        }
        root.setAttribute("data-bs-theme", theme);
        root.dataset.themePref = pref;
        el.themeSwitch.querySelectorAll("button").forEach((b) =>
            b.setAttribute("aria-checked", String(b.dataset.theme === pref)));
        try { localStorage.setItem("mm-theme", pref); } catch (e) { /* storage unavailable */ }
    }

    function setAutoRefresh(on) {
        clearInterval(state.timer);
        state.countdown = AUTO_REFRESH_SECONDS;
        el.countdown.textContent = on ? `${state.countdown}s` : "";
        if (!on) return;
        state.timer = setInterval(() => {
            state.countdown -= 1;
            if (state.countdown <= 0) {
                state.countdown = AUTO_REFRESH_SECONDS;
                loadEvents({ silent: true });
                if (document.querySelector("#tabLog.active")) loadLog();
            }
            el.countdown.textContent = `${state.countdown}s`;
        }, 1000);
    }

    /* ---------------- Sport Oracle ---------------- */

    const ORACLE_KEY = "mm-oracle";
    const desktop = window.matchMedia("(min-width: 1200px)");
    const oracleSportKeys = () => Object.keys(state.sports).filter((key) => state.sports[key].oracle);

    function initOracle() {
        let saved = {};
        try { saved = JSON.parse(localStorage.getItem(ORACLE_KEY) || "{}"); } catch (e) { /* storage unavailable */ }
        const supported = oracleSportKeys();
        if (!supported.length) return;
        // Start from the last Oracle choice, else the main view's sport if the Oracle supports it
        const sport = supported.includes(saved.sport) ? saved.sport
            : supported.includes(state.sport) ? state.sport : supported[0];
        const leagues = state.sports[sport].leagues;
        let league = leagues[0].id;
        if (saved.sport === sport && leagues.some((l) => l.id === saved.league)) league = saved.league;
        else if (sport === state.sport && leagues.some((l) => l.id === state.league)) league = state.league;
        Object.assign(state.oracle, { sport, league });
        renderOracleControls();
        if (desktop.matches) loadOracle();
    }

    function saveOracleChoice() {
        try {
            localStorage.setItem(ORACLE_KEY, JSON.stringify({ sport: state.oracle.sport, league: state.oracle.league }));
        } catch (e) { /* storage unavailable */ }
    }

    function renderOracleControls() {
        el.oracleSports.innerHTML = oracleSportKeys().map((key) => {
            const sport = state.sports[key];
            return `<button data-osport="${esc(key)}" class="${key === state.oracle.sport ? "active" : ""}" title="${esc(sport.name)}">
                        <span class="em" aria-hidden="true">${sport.emoji}</span>${esc(sport.name.split(" ")[0])}</button>`;
        }).join("");
        el.oracleLeague.innerHTML = state.sports[state.oracle.sport].leagues
            .map((l) => `<option value="${esc(l.id)}">${esc(l.name)}</option>`).join("");
        el.oracleLeague.value = state.oracle.league;
    }

    async function loadOracle({ fresh = false } = {}) {
        const id = ++state.oracle.requestId;
        state.oracle.loaded = true;
        el.oracleRefresh.classList.add("spinning");
        el.oracleList.innerHTML = '<div class="oracle-skeleton"></div>'.repeat(4);

        const params = new URLSearchParams({ sport: state.oracle.sport, league: state.oracle.league });
        if (fresh) params.set("fresh", "1");
        try {
            const data = await api(`/api/oracle?${params}`);
            if (id !== state.oracle.requestId) return;
            renderOracle(data);
        } catch (error) {
            if (id !== state.oracle.requestId) return;
            el.oracleList.innerHTML = `<div class="oracle-empty"><span class="em">🌫️</span>The Oracle's vision is clouded.<br>${esc(error.message)}</div>`;
        } finally {
            if (id === state.oracle.requestId) el.oracleRefresh.classList.remove("spinning");
        }
    }

    function renderOracle(data) {
        state.oracle.data = data;
        if (!data.fixtures.length) {
            el.oracleList.innerHTML = `
                <div class="oracle-empty"><span class="em">🗓️</span>
                    No upcoming fixtures in ESPN's calendar for ${esc(data.name)}.<br>The season may be over or between rounds.
                </div>`;
            return;
        }
        const combat = data.sport === "combat";
        const legend = combat
            ? '<span><i style="background:var(--oracle)"></i>First fighter</span><span><i style="background:var(--away)"></i>Second fighter</span>'
            : '<span><i style="background:var(--oracle)"></i>Home</span><span><i style="background:var(--muted-2)"></i>Draw</span><span><i style="background:var(--away)"></i>Away</span>';
        // For MMA, show each fight card's name once as a heading instead of on every bout
        let lastCard = null;
        const cards = data.fixtures.map((f, i) => {
            let heading = "";
            if (combat) {
                const cardName = f.context.slice(0, f.context.lastIndexOf(" · "));
                if (cardName !== lastCard) heading = `<div class="oracle-group" title="${esc(cardName)}">🥊 ${esc(cardName)}</div>`;
                lastCard = cardName;
            }
            return heading + oracleCard(f, i, combat);
        });
        el.oracleList.innerHTML = `<div class="oracle-legend">${legend}</div>`
            + '<div class="oracle-hint"><i class="bi bi-hand-index"></i> Click a match for details &amp; bookmaker odds</div>'
            + cards.join("");
    }

    function oracleCard(fixture, index, combat) {
        const p = fixture.prediction;
        const pr = p.probabilities;
        const pct = (v) => `${Math.round(v * 100)}%`;
        const date = new Date(fixture.date);

        const team = (side, key) => {
            const abbr = side.abbr || side.name.slice(0, 3).toUpperCase();
            const visual = side.logo
                ? `<img class="logo" src="${esc(side.logo)}" alt="" loading="lazy" data-abbr="${esc(abbr)}">`
                : side.flag ? `<img class="flag" src="${esc(side.flag)}" alt="" loading="lazy">`
                : `<span class="badge-abbr">${esc(abbr)}</span>`;
            const picked = p.pick === key;
            return `
                <div class="oc-team ${key} ${picked ? "picked" : ""}">
                    ${visual}
                    <span class="nm" title="${esc(side.name)}">${esc(side.name)}${picked ? '<i class="bi bi-stars" title="Oracle pick"></i>' : ""}</span>
                    <span class="pct">${pct(pr[key])}</span>
                </div>`;
        };

        const heads = combat ? ["", "1st", "2nd"] : ["", fixture.home.abbr || "Home", fixture.away.abbr || "Away"];
        const rows = p.comparison.map((c) => `<tr><td>${esc(c.label)}</td><td>${esc(c.home)}</td><td>${esc(c.away)}</td></tr>`).join("");
        const drawText = !combat && p.pick !== "draw" ? `<span>· Draw ${pct(pr.draw)}</span>` : "";

        return `
            <article class="oracle-card" data-fx="${index}" role="button" tabindex="0"
                     aria-label="Open details and odds: ${esc(fixture.home.name)} v ${esc(fixture.away.name)}"
                     style="animation-delay:${Math.min(index * 35, 350)}ms">
                <div class="oc-meta">
                    <span class="ctx" title="${esc(combat ? fixture.context : fixture.venue)}">${esc(fmtShortDay(date))} · ${esc(fmtTime(date))}${combat ? ` · ${esc(fixture.context.slice(fixture.context.lastIndexOf(" · ") + 3))}` : ""}</span>
                    <span class="when">${esc(relativeTime(date))}</span>
                </div>
                ${team(fixture.home, "home")}
                ${team(fixture.away, "away")}
                <div class="oc-bar" title="${combat ? "" : `Home ${pct(pr.home)} · Draw ${pct(pr.draw)} · Away ${pct(pr.away)}`}">
                    <span class="h" style="width:${pr.home * 100}%"></span>
                    ${pr.draw > 0.005 ? `<span class="d" style="width:${pr.draw * 100}%"></span>` : ""}
                    <span class="a" style="width:${pr.away * 100}%"></span>
                </div>
                <div class="oc-pick">
                    <i class="bi bi-stars"></i>Pick: <strong>${esc(p.pick_name)}</strong> ${drawText}
                    <span class="conf ${p.confidence.toLowerCase()}">${esc(p.confidence)}</span>
                </div>
                <details class="oc-why">
                    <summary>Why this pick?</summary>
                    <table>
                        <tr><th></th><th>${esc(heads[1])}</th><th>${esc(heads[2])}</th></tr>
                        ${rows}
                    </table>
                    <p class="note">${esc(p.note)}</p>
                </details>
            </article>`;
    }

    function bindOracleEvents() {
        el.oracleSports.addEventListener("click", (e) => {
            const button = e.target.closest("[data-osport]");
            if (!button || button.dataset.osport === state.oracle.sport) return;
            state.oracle.sport = button.dataset.osport;
            state.oracle.league = state.sports[state.oracle.sport].leagues[0].id;
            saveOracleChoice();
            renderOracleControls();
            loadOracle();
        });
        el.oracleLeague.addEventListener("change", () => {
            state.oracle.league = el.oracleLeague.value;
            saveOracleChoice();
            loadOracle();
        });
        el.oracleRefresh.addEventListener("click", () => loadOracle({ fresh: true }));
        // Clicking a prediction card (but not its "Why this pick?" toggle) opens the match modal
        const openFromCard = (e) => {
            if (e.target.closest("details")) return;
            const card = e.target.closest(".oracle-card[data-fx]");
            const fixture = card && state.oracle.data?.fixtures[+card.dataset.fx];
            if (fixture) openMatch(fixture);
        };
        el.oracleList.addEventListener("click", openFromCard);
        el.oracleList.addEventListener("keydown", (e) => {
            if ((e.key === "Enter" || e.key === " ") && e.target.matches(".oracle-card[data-fx]")) {
                e.preventDefault();
                openFromCard(e);
            }
        });
        // Below 1200px the Oracle is a drawer: load it the first time it opens
        el.oracle.addEventListener("show.bs.offcanvas", () => { if (!state.oracle.loaded && state.oracle.sport) loadOracle(); });
        desktop.addEventListener("change", () => { if (desktop.matches && !state.oracle.loaded && state.oracle.sport) loadOracle(); });
        el.oracleList.addEventListener("error", (e) => {
            const img = e.target;
            if (img.tagName !== "IMG") return;
            const badge = document.createElement("span");
            badge.className = "badge-abbr";
            badge.textContent = img.dataset.abbr || "?";
            img.replaceWith(badge);
        }, true);
    }

    /* ---------------- Match modal & bet view ---------------- */

    const ODDS_FORMAT_KEY = "mm-odds-format";
    // Common UK fractional prices, used to show decimal odds the way bookmakers do
    const FRACTIONS = ["1/10", "1/8", "2/11", "1/5", "2/9", "1/4", "2/7", "3/10", "1/3", "4/11", "2/5", "4/9", "1/2",
        "8/15", "4/7", "8/13", "4/6", "8/11", "4/5", "5/6", "10/11", "1/1", "21/20", "11/10", "6/5", "5/4", "11/8",
        "6/4", "13/8", "7/4", "15/8", "2/1", "85/40", "9/4", "5/2", "11/4", "3/1", "10/3", "7/2", "4/1", "9/2", "5/1",
        "11/2", "6/1", "13/2", "7/1", "15/2", "8/1", "9/1", "10/1", "11/1", "12/1", "14/1", "16/1", "20/1", "25/1",
        "33/1", "40/1", "50/1", "66/1", "100/1"];

    function oddsFormat() {
        try { return localStorage.getItem(ODDS_FORMAT_KEY) || "fractional"; } catch (e) { return "fractional"; }
    }

    function formatPrice(decimal) {
        if (!decimal) return "–";
        if (oddsFormat() === "decimal") return decimal.toFixed(2);
        let best = FRACTIONS[0], gap = Infinity;
        for (const f of FRACTIONS) {
            const [n, d] = f.split("/").map(Number);
            const diff = Math.abs(1 + n / d - decimal);
            if (diff < gap) { gap = diff; best = f; }
        }
        return best === "1/1" ? "Evens" : best;
    }

    const pct = (v) => `${Math.round(v * 100)}%`;

    function openMatch(fixture) {
        Object.assign(state.match, {
            mode: "oracle", fixture, sport: state.oracle.sport, league: state.oracle.league,
            leagueName: state.oracle.data?.name || "", detail: null, odds: null, view: "detail",
        });
        renderMatchModal();
        bootstrap.Modal.getOrCreateInstance(el.matchModal).show();
        if (state.match.sport !== "combat") loadMatchDetail();
    }

    async function loadMatchDetail() {
        const id = ++state.match.requestId;
        const { sport, league, fixture } = state.match;
        try {
            const detail = await api(`/api/match?${new URLSearchParams({ sport, league, id: fixture.id })}`);
            if (id !== state.match.requestId) return;
            state.match.detail = detail;
        } catch (error) {
            if (id !== state.match.requestId) return;
            state.match.detail = { error: error.message };
        }
        if (state.match.view === "detail") renderMatchModal();
    }

    async function loadOdds() {
        const { sport, league, fixture } = state.match;
        const params = new URLSearchParams({
            sport, league, id: fixture.id, home: fixture.home.name, away: fixture.away.name, date: fixture.date,
        });
        try {
            state.match.odds = await api(`/api/odds?${params}`);
        } catch (error) {
            state.match.odds = { error: error.message };
        }
        if (state.match.fixture === fixture && state.match.view === "bet") renderMatchModal();
    }

    function renderMatchModal() {
        if (state.match.mode === "game") { renderGameModal(); return; }
        const { fixture, view, leagueName } = state.match;
        const betting = view === "bet";
        el.betBtn.classList.remove("d-none");
        el.mmEyebrow.textContent = betting ? "Where to bet" : leagueName;
        el.mmTitle.textContent = `${fixture.home.name} v ${fixture.away.name}`;
        el.betBack.classList.toggle("d-none", !betting);
        el.betBtn.classList.toggle("back", betting);
        el.betBtn.innerHTML = betting ? '<i class="bi bi-arrow-left"></i> Back to match' : '<i class="bi bi-cash-coin"></i> Bet';
        el.mmFootNote.innerHTML = betting
            ? '18+ · Bets are placed on the bookmaker\'s own site · <a href="https://www.begambleaware.org" target="_blank" rel="noopener noreferrer">BeGambleAware.org</a>'
            : "See bookmaker odds for this match";
        el.mmBody.innerHTML = betting ? betViewHtml() : matchDetailHtml();
        el.mmBody.scrollTop = 0;
    }

    function sideVisual(side) {
        const abbr = side.abbr || side.name.slice(0, 3).toUpperCase();
        if (side.logo) return `<img class="crest" src="${esc(side.logo)}" alt="" data-abbr="${esc(abbr)}">`;
        if (side.flag) return `<img class="flag-lg" src="${esc(side.flag)}" alt="">`;
        return `<span class="badge-lg">${esc(abbr)}</span>`;
    }

    function matchDetailHtml() {
        const { fixture, detail, sport } = state.match;
        const combat = sport === "combat";
        const p = fixture.prediction;
        const pr = p.probabilities;
        const date = new Date(fixture.date);
        const venue = detail?.venue?.name
            ? [detail.venue.name, detail.venue.city, detail.venue.country].filter(Boolean).join(", ")
            : fixture.venue;
        const sub = (side) => combat ? [side.record, side.country].filter(Boolean).join(" · ") : (side.form ? `Form ${side.form}` : "");

        const outcomes = combat ? ["home", "away"] : ["home", "draw", "away"];
        const labels = { home: fixture.home.name, draw: "Draw", away: fixture.away.name };
        const heads = combat ? ["Fighter 1", "Fighter 2"] : [fixture.home.abbr || "Home", fixture.away.abbr || "Away"];

        let html = `
            <div class="mm-hero">
                <div class="mm-side">${sideVisual(fixture.home)}<div class="nm">${esc(fixture.home.name)}</div><div class="sub">${esc(sub(fixture.home))}</div></div>
                <div class="mm-centre">
                    <div class="time">${esc(fmtTime(date))}</div>
                    <div class="day">${esc(fmtDay(date))}</div>
                    <div class="cd">${esc(relativeTime(date))}</div>
                </div>
                <div class="mm-side">${sideVisual(fixture.away)}<div class="nm">${esc(fixture.away.name)}</div><div class="sub">${esc(sub(fixture.away))}</div></div>
            </div>
            ${venue ? `<div class="mm-venue"><i class="bi bi-geo-alt"></i> ${esc(venue)}${combat ? ` · ${esc(fixture.context)}` : ""}</div>` : ""}

            <section class="mm-section">
                <h3><i class="bi bi-stars"></i>Oracle prediction</h3>
                <div class="mm-card">
                    <div class="mm-prob" style="--cols:${outcomes.length}">
                        ${outcomes.map((o) => `<div class="${o}"><div class="v">${pct(pr[o])}</div><div class="l" title="${esc(labels[o])}">${esc(labels[o])}</div></div>`).join("")}
                    </div>
                    <div class="oc-bar mm-bar">
                        <span class="h" style="width:${pr.home * 100}%"></span>
                        ${pr.draw > 0.005 ? `<span class="d" style="width:${pr.draw * 100}%"></span>` : ""}
                        <span class="a" style="width:${pr.away * 100}%"></span>
                    </div>
                    <div class="oc-pick"><i class="bi bi-stars"></i>Pick: <strong>${esc(p.pick_name)}</strong>
                        <span class="conf ${p.confidence.toLowerCase()}">${esc(p.confidence)}</span></div>
                    <table class="mm-cmp">
                        <tr><td>${esc(heads[0])}</td><td></td><td>${esc(heads[1])}</td></tr>
                        ${p.comparison.map((c) => `<tr><td>${esc(c.home)}</td><td>${esc(c.label)}</td><td>${esc(c.away)}</td></tr>`).join("")}
                    </table>
                    <div class="mm-muted mt-2">${esc(p.note)}</div>
                </div>
            </section>`;

        if (combat) {
            return html + `<p class="mm-muted mt-3"><i class="bi bi-info-circle"></i> ESPN doesn't publish detailed fight previews, so this view uses the fighters' career records.</p>`;
        }
        if (!detail) return html + '<div class="mm-skel"></div><div class="mm-skel"></div>';
        if (detail.error) return html + `<p class="mm-muted mt-3"><i class="bi bi-exclamation-circle"></i> Couldn't load more detail: ${esc(detail.error)}</p>`;

        return html + formSectionHtml(detail, fixture.home, fixture.away) + contextSectionsHtml(detail, fixture.home, fixture.away);
    }

    /* ----- results modal: any game from the main results list ----- */

    function openGame(event) {
        Object.assign(state.match, {
            mode: "game", event, fixture: null, sport: state.sport, league: state.league,
            leagueName: event.competition || state.data?.name || "", detail: null, odds: null, view: "detail",
        });
        renderMatchModal();
        bootstrap.Modal.getOrCreateInstance(el.matchModal).show();
        // F1 and MMA have no ESPN summary; their views use the results already loaded
        if (["football", "rugby", "cricket"].includes(state.sport) && /^\d+$/.test(event.id)) loadGameDetail();
    }

    async function loadGameDetail() {
        const id = ++state.match.requestId;
        const { sport, league, event } = state.match;
        const params = new URLSearchParams({ sport, league, id: event.id });
        if (event.league_id) params.set("cleague", event.league_id);
        try {
            const detail = await api(`/api/game?${params}`);
            if (id !== state.match.requestId) return;
            state.match.detail = detail;
        } catch (error) {
            if (id !== state.match.requestId) return;
            state.match.detail = { error: error.message };
        }
        renderMatchModal();
    }

    function renderGameModal() {
        const { event, leagueName } = state.match;
        el.betBack.classList.add("d-none");
        el.betBtn.classList.add("d-none");
        el.mmEyebrow.textContent = [leagueName, event.status].filter(Boolean).join(" · ");
        el.mmTitle.textContent = event.kind === "match" ? `${event.home.name} v ${event.away.name}` : event.name;
        el.mmFootNote.textContent = "Data from ESPN";
        const body = { race: raceGameHtml, card: fightGameHtml }[event.kind]
            || (state.match.sport === "cricket" ? cricketGameHtml : teamGameHtml);
        el.mmBody.innerHTML = body(event, state.match.detail);
    }

    const detailPlaceholder = (detail) => !detail ? '<div class="mm-skel"></div><div class="mm-skel"></div>'
        : detail.error ? `<p class="mm-muted mt-3"><i class="bi bi-exclamation-circle"></i> Couldn't load more detail: ${esc(detail.error)}</p>` : "";

    function gameHero(event, centre) {
        const sub = (side) => side.form ? `Form ${side.form.slice(-5)}` : side.record || "";
        return `
            <div class="mm-hero">
                <div class="mm-side">${sideVisual(event.home)}<div class="nm">${esc(event.home.name)}</div><div class="sub">${esc(sub(event.home))}</div></div>
                <div class="mm-centre">${centre}</div>
                <div class="mm-side">${sideVisual(event.away)}<div class="nm">${esc(event.away.name)}</div><div class="sub">${esc(sub(event.away))}</div></div>
            </div>`;
    }

    function scoreCentre(event) {
        const date = new Date(event.date);
        if (event.state === "pre") {
            return `<div class="time">${esc(fmtTime(date))}</div><div class="day">${esc(fmtDay(date))}</div><div class="cd">${esc(relativeTime(date))}</div>`;
        }
        const hs = +event.home.score, as = +event.away.score;
        return `
            <div class="mm-score">
                <span class="${event.completed && hs < as ? "dim" : ""}">${esc(event.home.score)}</span><span class="sep">–</span>
                <span class="${event.completed && as < hs ? "dim" : ""}">${esc(event.away.score)}</span>
            </div>
            <div class="mt-2">${statusPill(event)}</div>
            <div class="day">${esc(fmtDay(date))}</div>`;
    }

    function eventIcon(type) {
        const t = type.toLowerCase();
        if (t.includes("yellow")) return '<span class="card-y" title="Yellow card"></span>';
        if (t.includes("red")) return '<span class="card-red" title="Red card"></span>';
        if (t.includes("substitution")) return '<i class="bi bi-arrow-left-right ico-sub" title="Substitution"></i>';
        if (t === "try") return '<span class="tag-try" title="Try">T</span>';
        return '<i class="bi bi-record-circle ico-goal" title="Goal"></i>';
    }

    function timelineHtml(events) {
        if (!events.length) return "";
        const item = (e) => {
            const tag = /own goal/i.test(e.type) ? " (OG)" : /penalty/i.test(e.type) ? " (P)" : "";
            const body = e.on
                ? `<span class="in">↑ ${esc(e.on)}</span><small class="out">↓ ${esc(e.off)}</small>`
                : `${esc(e.player)}${tag}`;
            return `<span class="ev">${eventIcon(e.type)}<span>${body}</span></span>`;
        };
        return section("bi-clock-history", "Match events", `
            <div class="mm-card"><ul class="timeline">${events.map((e) => `
                <li><div class="h">${e.side === "home" ? item(e) : ""}</div>
                    <div class="mn">${esc(e.minute)}</div>
                    <div class="a">${e.side === "away" ? item(e) : ""}</div></li>`).join("")}
            </ul></div>`);
    }

    function statsHtml(stats) {
        if (!stats?.length) return "";
        return section("bi-bar-chart", "Team stats", `
            <div class="mm-card">${stats.map((s) => {
                const total = s.home_value + s.away_value || 1;
                return `
                    <div class="stat-row">
                        <div class="top"><span>${esc(s.home)}</span><span>${esc(s.label)}</span><span>${esc(s.away)}</span></div>
                        <div class="stat-bars"><span class="h" style="flex:${s.home_value / total || 0.0001}"></span><span class="a" style="flex:${s.away_value / total || 0.0001}"></span></div>
                    </div>`;
            }).join("")}</div>`);
    }

    function lineupsHtml(lineups, event) {
        if (!lineups?.length) return "";
        const player = (p) => `
            <li><span class="no">${esc(p.jersey)}</span>
                <span class="text-truncate">${esc(p.name)}${p.captain ? ' <span class="cap" title="Captain">C</span>' : ""}
                    ${p.subbed_out ? '<span class="out" title="Substituted off">↓</span>' : ""}${p.subbed_in ? '<span class="in" title="Came on">↑</span>' : ""}</span>
                <span class="pos">${esc(p.position)}</span></li>`;
        const block = (side) => {
            const team = lineups.find((l) => l.side === side) || {};
            const name = side === "home" ? event.home.name : event.away.name;
            return `
                <div class="mm-card lineup">
                    <h4><span class="text-truncate">${esc(team.team || name)}</span>${team.formation ? `<small>${esc(team.formation)}</small>` : ""}</h4>
                    <ul>${(team.starters || []).map(player).join("")}</ul>
                    ${team.bench?.length ? `<div class="bench-title">${state.match.sport === "rugby" ? "Replacements" : "Substitutes"}</div><ul>${team.bench.map(player).join("")}</ul>` : ""}
                </div>`;
        };
        return section("bi-people", "Line-ups", `<div class="mm-two">${block("home")}${block("away")}</div>`);
    }

    function teamGameHtml(event, detail) {
        const venue = detail?.venue?.name ? [detail.venue.name, detail.venue.city].filter(Boolean).join(", ") : event.venue;
        const attendance = detail?.attendance ? ` · Attendance ${Number(detail.attendance).toLocaleString()}` : "";
        let html = gameHero(event, scoreCentre(event))
            + (venue ? `<div class="mm-venue"><i class="bi bi-geo-alt"></i> ${esc(venue)}${esc(attendance)}</div>` : "");
        // Rugby summaries have no key events, so fall back to the tries and cards from the results feed
        const events = detail?.key_events?.length ? detail.key_events
            : (event.incidents || []).map((i) => ({ minute: i.minute, type: i.type === "red_card" ? "Red Card" : i.label, side: i.side, player: i.player }));
        if (event.state !== "pre") html += timelineHtml(events);
        if (!detail || detail.error) return html + detailPlaceholder(detail);
        html += statsHtml(detail.stats) + lineupsHtml(detail.lineups, event);
        if (event.state === "pre") html += formSectionHtml(detail, event.home, event.away);
        return html + contextSectionsHtml(detail, event.home, event.away);
    }

    function cricketGameHtml(event, detail) {
        const team = (side) => `
            <div class="cricket-row ${event.completed && (event.home.winner || event.away.winner) ? (side.winner ? "won" : "lost") : ""}">
                ${side.logo ? `<img class="team-logo" src="${esc(side.logo)}" alt="" data-abbr="${esc(side.abbr)}">` : `<div class="team-logo-fallback">${esc(side.abbr)}</div>`}
                <div class="cname">${esc(side.name)}</div><div class="cscore">${esc(side.score || "–")}</div>
            </div>`;
        let html = `
            <div class="mm-card cricket-hero">
                ${team(event.home)}${team(event.away)}
                ${event.summary ? `<div class="match-summary px-0 pb-0">${esc(event.summary)}</div>` : ""}
            </div>
            <div class="mm-venue mt-2"><i class="bi bi-geo-alt"></i> ${esc(detail?.venue?.name || event.venue)}${event.description ? ` · ${esc(event.description)}` : ""}</div>`;
        if (!detail || detail.error) return html + detailPlaceholder(detail);

        if (detail.notes.length) {
            html += section("bi-info-circle", "Match notes", `<dl class="settings-list mm-notes m-0">${detail.notes.map((n) => `<dt>${esc(n.label)}</dt><dd>${esc(n.text)}</dd>`).join("")}</dl>`);
        }
        const columns = {
            Batting: [["Batter", "playerName", "l"], ["", "dismissal", "l dis"], ["R", "runs"], ["B", "ballsFaced"], ["4s", "fours"], ["6s", "sixes"]],
            Bowling: [["Bowler", "playerName", "l"], ["O", "overs"], ["M", "maidens"], ["R", "conceded"], ["W", "wickets"], ["Econ", "economyRate"]],
            Partnerships: [["Wicket", "partnershipWicketName", "l"], ["Partners", null, "l"], ["Runs", "partnershipRuns"], ["Overs", "partnershipOvers"]],
        };
        const cell = (row, [, key, cls = ""]) => {
            const value = key ? row[key] : `${row.player1Name} ${row.player1Runs} & ${row.player2Name} ${row.player2Runs}`;
            return `<td class="${cls}">${esc(value ?? "")}</td>`;
        };
        const cards = detail.scorecards.filter((c) => columns[c.type]).map((c) => `
            <div class="sc-head"><span>${esc(c.type)} · ${esc(c.team)}</span><small>Innings ${esc(c.innings)}${c.runs ? ` · ${esc(c.runs)} ${esc(c.total)}` : ""}</small></div>
            <div class="mm-table-wrap"><table class="mm-table sc">
                <thead><tr>${columns[c.type].map(([label, , cls = ""]) => `<th class="${cls}">${esc(label)}</th>`).join("")}</tr></thead>
                <tbody>${c.rows.map((row) => `<tr>${columns[c.type].map((col) => cell(row, col)).join("")}</tr>`).join("")}
                ${c.extras ? `<tr><td class="l" colspan="2">Extras</td><td>${esc(c.extras)}</td><td colspan="3"></td></tr>` : ""}</tbody>
            </table></div>`).join("");
        if (cards) html += section("bi-table", "Scorecard (latest innings)", cards);
        return html + contextSectionsHtml({ news: detail.news }, event.home, event.away);
    }

    function raceGameHtml(event) {
        const start = new Date(event.date);
        const end = event.end_date ? new Date(event.end_date) : start;
        const open = pickSession(event);
        let html = `
            <div class="mm-venue mb-3"><i class="bi bi-geo-alt"></i> ${esc(event.venue)}${event.location ? `, ${esc(event.location)}` : ""}
                · ${esc(fmtShortDay(start))}${end.toDateString() !== start.toDateString() ? ` – ${esc(fmtShortDay(end))}` : ""}</div>`;
        html += event.sessions.map((s) => {
            const d = new Date(s.date);
            const rows = s.results.map((r) => `
                <tr class="${r.position <= 3 ? "hl" : ""}"><td>${r.position}</td>
                    <td class="l">${r.flag ? `<img class="flag-sm" src="${esc(r.flag)}" alt="">` : ""}${esc(r.name)}</td>
                    <td class="l">${esc(r.country)}</td></tr>`).join("");
            return `
                <details class="mm-card session-block" ${s === open ? "open" : ""}>
                    <summary><strong>${esc(s.name)}</strong><span>${esc(fmtShortDay(d))}, ${esc(fmtTime(d))} · ${esc(s.status)}</span></summary>
                    ${rows ? `<div class="mm-table-wrap mt-2"><table class="mm-table sc">
                        <thead><tr><th>Pos</th><th class="l">Driver</th><th class="l">Nationality</th></tr></thead><tbody>${rows}</tbody>
                    </table></div>` : `<div class="mm-muted mt-2">${s.state === "pre" ? `Starts ${esc(relativeTime(d))}.` : "No results published."}</div>`}
                </details>`;
        }).join("");
        return html + '<p class="mm-muted mt-3"><i class="bi bi-info-circle"></i> ESPN publishes finishing order and nationality only, without lap times or teams.</p>';
    }

    function fightGameHtml(event) {
        const date = new Date(event.date);
        const rows = event.bouts.map((b, i) => {
            const [a, z] = [b.fighters[0] || { name: "TBC" }, b.fighters[1] || { name: "TBC" }];
            return `
                <li class="bout ${i === 0 ? "main-event" : ""}">
                    ${fighterBlock(a, b, false)}
                    <div class="bout-mid"><span class="weight-tag">${i === 0 ? "Main event" : esc(b.weight_class || "Bout")}</span>${boutResult(b)}
                        ${b.scheduled_rounds ? `<div class="bout-result">${esc(b.scheduled_rounds)}-round bout</div>` : ""}</div>
                    ${fighterBlock(z, b, true)}
                </li>`;
        }).join("");
        return `
            <div class="mm-venue mb-3"><i class="bi bi-geo-alt"></i> ${esc(event.venue || "Venue TBC")}${event.location ? `, ${esc(event.location)}` : ""}
                · ${esc(fmtDay(date))}, ${esc(fmtTime(date))}</div>
            <div class="mm-card p-0"><ul class="bout-list">${rows}</ul></div>
            <p class="mm-muted mt-3"><i class="bi bi-info-circle"></i> ESPN lists the winner, round and time but not how the fight ended (KO, submission or decision).</p>`;
    }

    /* ----- sections shared by the Oracle modal and the results modal ----- */

    const section = (icon, title, body) => `<section class="mm-section"><h3><i class="bi ${icon}"></i>${esc(title)}</h3>${body}</section>`;
    const shortDate = (value) => value ? new Date(value).toLocaleDateString([], { day: "numeric", month: "short", year: "numeric" }) : "";

    function formSectionHtml(detail, home, away) {
        if (!detail.form?.length) return "";
        const formBlock = (side) => {
            const block = detail.form.find((f) => f.team_id === side.id) || detail.form.find((f) => f.team === side.name);
            const games = block?.games || [];
            return `
                <div class="mm-card">
                    <div class="mm-form-head">${side.logo ? `<img src="${esc(side.logo)}" alt="">` : ""}<span class="text-truncate">${esc(side.name)}</span></div>
                    ${games.length ? `<ul class="mm-form-list">${games.map((g) => `
                        <li title="${esc(g.competition)}">
                            <span class="res ${esc(g.result)}">${esc(g.result || "–")}</span>
                            <span class="opp"><small>${g.venue === "H" ? "vs" : "at"}</small>${esc(g.opponent)}</span>
                            <span class="sc">${esc(g.score)}</span>
                        </li>`).join("")}</ul>` : '<div class="mm-muted">No recent results.</div>'}
                </div>`;
        };
        return section("bi-activity", "Last 5 results", `<div class="mm-two">${formBlock(home)}${formBlock(away)}</div>`);
    }

    // Head-to-head, league table, where to watch and news
    function contextSectionsHtml(detail, home, away) {
        let html = "";
        const h2h = detail.head_to_head;
        if (h2h?.meetings.length) {
            html += section("bi-arrow-left-right", "Head to head", `
                <div class="mm-card">
                    ${h2h.summary ? `<div class="mm-h2h-sum">${esc(h2h.summary)}</div>` : ""}
                    <ul class="mm-h2h">${h2h.meetings.map((m) => `
                        <li><span class="dt">${esc(shortDate(m.date))}</span>
                            <span class="h">${esc(m.home)}</span><span class="s">${esc(m.home_score)}–${esc(m.away_score)}</span><span class="a">${esc(m.away)}</span></li>`).join("")}
                    </ul>
                </div>`);
        }
        if (detail.table?.length) {
            const ids = [home.id, away.id].filter(Boolean);
            const names = [home.name, away.name];
            html += section("bi-list-ol", "League table", `
                <div class="mm-table-wrap">
                    <table class="mm-table">
                        <thead><tr><th>#</th><th>Team</th><th>P</th><th>W</th><th>D</th><th>L</th><th>Diff</th><th>Pts</th></tr></thead>
                        <tbody>${detail.table.map((r) => `
                            <tr class="${ids.includes(r.team_id) || names.includes(r.team) ? "hl" : ""}">
                                <td>${r.rank}</td><td title="${esc(r.team)}">${esc(r.team)}</td><td>${r.played}</td><td>${r.won}</td>
                                <td>${r.drawn}</td><td>${r.lost}</td><td>${r.diff > 0 ? "+" : ""}${r.diff}</td><td>${r.points}</td>
                            </tr>`).join("")}</tbody>
                    </table>
                </div>`);
        }
        if (detail.broadcasts?.length) {
            html += section("bi-tv", "Where to watch", `<div class="mm-chips">${detail.broadcasts.map((b) => `<span>${esc(b)}</span>`).join("")}</div>`);
        }
        if (detail.news?.length) {
            html += section("bi-newspaper", "Latest news", `<ul class="mm-news">${detail.news.map((n) => `
                <li><a href="${esc(n.url)}" target="_blank" rel="noopener noreferrer"><i class="bi bi-box-arrow-up-right"></i>${esc(n.headline)}</a></li>`).join("")}</ul>`);
        }
        return html;
    }

    function betViewHtml() {
        const { fixture, odds, sport } = state.match;
        if (!odds) return '<div class="mm-muted mb-2"><span class="spinner-border spinner-border-sm"></span> Finding bookmaker prices…</div><div class="mm-skel"></div><div class="mm-skel"></div>';
        if (odds.error) return `<div class="bet-notice warn"><i class="bi bi-exclamation-triangle"></i><div>Couldn't load odds: ${esc(odds.error)}</div></div>`;

        const labels = { home: fixture.home.name, draw: "Draw", away: fixture.away.name };
        const format = oddsFormat();
        let html = `
            <div class="bet-intro">
                <div>
                    <h3>Bookmaker odds · ${sport === "combat" ? "fight winner" : "match result"}</h3>
                    <p>Compare prices, then place any bet on the bookmaker's own website. Sports Monitor doesn't take bets.</p>
                </div>
                <div class="segmented sm" id="oddsFormat" role="tablist" aria-label="Odds format">
                    <button data-format="fractional" class="${format === "fractional" ? "active" : ""}">Fractional</button>
                    <button data-format="decimal" class="${format === "decimal" ? "active" : ""}">Decimal</button>
                </div>
            </div>`;

        if (!odds.odds_api_configured) {
            html += `
                <div class="bet-notice"><i class="bi bi-info-circle"></i><div>
                    ${sport === "combat" ? "No odds source is set up for MMA yet." : "Showing ESPN's US odds only."}
                    To compare <strong>UK bookmakers</strong> (Bet365, William Hill, Paddy Power, Sky Bet…), add a free
                    <a href="https://the-odds-api.com" target="_blank" rel="noopener noreferrer">The Odds API</a> key, as described in the README under <em>Bookmaker odds</em>.
                </div></div>`;
        }
        odds.notes.forEach((note) => { html += `<div class="bet-notice warn"><i class="bi bi-exclamation-triangle"></i><div>${esc(note)}</div></div>`; });

        if (!odds.bookmakers.length) {
            return html + `<div class="oracle-empty"><span class="em">🧾</span>No bookmaker prices are available for this fixture yet.</div>`;
        }

        const comparing = odds.bookmakers.length > 1;  // "best price" only means something with 2+ bookmakers
        const rows = odds.bookmakers.map((b) => `
            <tr>
                <td class="bm">${esc(b.name)}<span class="region-tag" title="${b.region === "US" ? "US-only bookmaker" : "UK bookmaker"}">${esc(b.region)}</span>
                    <small>via ${esc(b.source)}</small></td>
                ${odds.outcomes.map((o) => `<td><span class="price ${comparing && b.odds[o] && b.odds[o] === odds.best[o] ? "best" : ""}">${esc(formatPrice(b.odds[o]))}</span></td>`).join("")}
                <td>${b.url ? `<a class="visit" href="${esc(b.url)}" target="_blank" rel="noopener noreferrer">Visit site <i class="bi bi-box-arrow-up-right"></i></a>` : ""}</td>
            </tr>`).join("");

        const compare = odds.implied ? `
            <tr class="cmp"><td>Bookmakers' chance<br><small>average, margin removed</small></td>
                ${odds.outcomes.map((o) => `<td><strong>${pct(odds.implied[o])}</strong></td>`).join("")}<td></td></tr>
            <tr class="cmp"><td>Sport Oracle</td>
                ${odds.outcomes.map((o) => `<td><strong>${pct(fixture.prediction.probabilities[o])}</strong></td>`).join("")}<td></td></tr>` : "";

        const retrieved = new Date(odds.retrieved);
        html += `
            <div class="odds-wrap">
                <table class="odds-table">
                    <thead><tr><th>Bookmaker</th>${odds.outcomes.map((o) => `<th title="${esc(labels[o])}">${esc(labels[o])}</th>`).join("")}<th></th></tr></thead>
                    <tbody>${rows}${compare}</tbody>
                </table>
            </div>
            <div class="best-legend">${comparing ? '<span class="price best">x</span> Best available price for that result · ' : ""}Sources: ${esc(odds.sources.join(", "))}</div>
            <p class="rg-note">
                Prices retrieved at ${esc(fmtTime(retrieved))} and may have changed. Check them on the bookmaker's site before betting.
                US-tagged bookmakers aren't available to UK customers. 18+ only. Please gamble responsibly. Free, confidential
                support is available from <a href="https://www.begambleaware.org" target="_blank" rel="noopener noreferrer">BeGambleAware.org</a>
                and the National Gambling Helpline (0808 8020 133).
            </p>`;
        return html;
    }

    function bindMatchModalEvents() {
        const toggleView = () => {
            state.match.view = state.match.view === "bet" ? "detail" : "bet";
            renderMatchModal();
            if (state.match.view === "bet" && !state.match.odds) loadOdds();
        };
        el.betBtn.addEventListener("click", toggleView);
        el.betBack.addEventListener("click", toggleView);
        el.mmBody.addEventListener("click", (e) => {
            const button = e.target.closest("#oddsFormat button[data-format]");
            if (!button) return;
            try { localStorage.setItem(ODDS_FORMAT_KEY, button.dataset.format); } catch (err) { /* storage unavailable */ }
            renderMatchModal();
        });
        el.mmBody.addEventListener("error", (e) => {
            const img = e.target;
            if (img.tagName !== "IMG") return;
            if (!img.dataset.abbr) { img.remove(); return; }
            const badge = document.createElement("span");
            badge.className = "badge-lg";
            badge.textContent = img.dataset.abbr;
            img.replaceWith(badge);
        }, true);
        el.matchModal.addEventListener("hidden.bs.modal", () => { state.match.requestId++; });
    }

    /* ---------------- events ---------------- */

    function bindEvents() {
        el.sportTabs.addEventListener("click", (e) => {
            const button = e.target.closest("[data-sport]");
            if (button) switchSport(button.dataset.sport);
        });

        el.leagueSelect.addEventListener("change", () => {
            const custom = el.leagueSelect.value === "custom";
            el.customLeague.classList.toggle("d-none", !custom);
            if (custom) { el.customLeague.focus(); return; }
            state.league = el.leagueSelect.value;
            state.search = "";
            state.expanded.clear();
            syncControls();
            loadEvents();
        });

        el.customLeague.addEventListener("keydown", (e) => {
            if (e.key !== "Enter") return;
            const code = el.customLeague.value.trim().toLowerCase();
            if (!/^[a-z]{3,4}\.[a-z0-9]+$/.test(code)) { toast("League codes look like 'bel.1' or 'uefa.champions'", "error"); return; }
            state.league = code;
            state.search = "";
            syncControls();
            loadEvents();
        });

        el.dateInput.addEventListener("change", () => {
            state.date = fromInputDate(el.dateInput.value);
            syncControls();
            loadEvents();
        });
        el.prevDay.addEventListener("click", () => shiftDay(-1));
        el.nextDay.addEventListener("click", () => shiftDay(1));
        el.matchdayBtn.addEventListener("click", () => {
            if (!state.date) return;
            state.date = "";
            syncControls();
            loadEvents();
        });

        const onSearchChange = debounce(() => {
            const value = el.teamInput.value.trim();
            if (value === state.search) return;
            state.search = value;
            el.clearTeam.classList.toggle("d-none", !value);
            renderSettings();
            loadEvents();
        }, 450);
        el.teamInput.addEventListener("input", onSearchChange);
        el.clearTeam.addEventListener("click", () => {
            el.teamInput.value = "";
            onSearchChange();
            el.teamInput.focus();
        });

        el.statusFilter.addEventListener("click", (e) => {
            const button = e.target.closest("button[data-status]");
            if (!button) return;
            state.status = button.dataset.status;
            syncControls();
            if (state.data) renderEventList();
        });

        const openFromResult = (e) => {
            if (e.target.closest("a, details")) return;
            const card = e.target.closest("[data-ev]");
            const event = card && state.shown?.[+card.dataset.ev];
            if (event) openGame(event);
        };
        el.matches.addEventListener("keydown", (e) => {
            if ((e.key === "Enter" || e.key === " ") && e.target.matches("[data-ev]")) {
                e.preventDefault();
                openFromResult(e);
            }
        });
        el.matches.addEventListener("click", (e) => {
            const target = e.target.closest("[data-action]");
            if (!target) { openFromResult(e); return; }
            const action = target.dataset.action;
            if (action === "retry") loadEvents();
            if (action === "session") {
                state.f1Session[target.dataset.event] = target.dataset.code;
                renderEventList();
            }
            if (action === "expand") {
                const key = target.dataset.key;
                state.expanded.has(key) ? state.expanded.delete(key) : state.expanded.add(key);
                renderEventList();
            }
        });
        // Swap broken team logos for an abbreviation badge, and hide broken flags
        el.matches.addEventListener("error", (e) => {
            const img = e.target;
            if (img.tagName !== "IMG") return;
            if (!img.dataset.abbr) { img.remove(); return; }
            const badge = document.createElement("div");
            badge.className = "team-logo-fallback";
            badge.textContent = img.dataset.abbr;
            img.replaceWith(badge);
        }, true);

        el.refreshBtn.addEventListener("click", () => loadEvents({ silent: !!state.data }));
        el.autoRefresh.addEventListener("change", () => setAutoRefresh(el.autoRefresh.checked));
        el.themeSwitch.addEventListener("click", (e) => {
            const button = e.target.closest("button[data-theme]");
            if (button) setTheme(button.dataset.theme);
        });
        // In System mode, follow the OS when it switches between light and dark
        systemDark.addEventListener("change", () => {
            if (document.documentElement.dataset.themePref === "system") setTheme("system");
        });

        el.generateBtn.addEventListener("click", generateReport);
        el.saveConfigBtn.addEventListener("click", saveConfig);
        el.downloadReport.addEventListener("click", downloadReport);
        el.copyReport.addEventListener("click", async () => {
            try {
                await navigator.clipboard.writeText(state.report.content);
                toast("Report copied to clipboard");
            } catch (e) {
                toast("Clipboard is not available", "error");
            }
        });

        el.logTabBtn.addEventListener("shown.bs.tab", loadLog);
        el.refreshLog.addEventListener("click", loadLog);
        el.logFilter.addEventListener("click", (e) => {
            const button = e.target.closest("button[data-level]");
            if (!button) return;
            state.logLevel = button.dataset.level;
            el.logFilter.querySelectorAll("button").forEach((b) => b.classList.toggle("active", b === button));
            renderLog();
        });

        document.addEventListener("keydown", (e) => {
            const typing = /^(INPUT|SELECT|TEXTAREA)$/.test(document.activeElement?.tagName);
            if (typing || e.metaKey || e.ctrlKey || e.altKey) return;
            const key = e.key.toLowerCase();
            if (key === "/") { e.preventDefault(); el.teamInput.focus(); el.teamInput.select(); }
            if (key === "r") loadEvents({ silent: !!state.data });
            if (key === "t") setTheme(document.documentElement.getAttribute("data-bs-theme") === "dark" ? "light" : "dark");
            if (key === "o" && !desktop.matches) bootstrap.Offcanvas.getOrCreateInstance(el.oracle).toggle();
            const sportKeys = Object.keys(state.sports);
            if (/^[1-9]$/.test(key) && sportKeys[+key - 1]) switchSport(sportKeys[+key - 1]);
        });
    }

    /* ---------------- start ---------------- */

    async function init() {
        setTheme(document.documentElement.dataset.themePref || "system", { animate: false });
        bindEvents();
        bindOracleEvents();
        bindMatchModalEvents();
        try {
            state.sports = await api("/api/sports");
        } catch (error) {
            renderError(`Unable to load the sport list: ${error.message}`);
            return;
        }
        try {
            const config = await api("/api/config");
            state.sport = config.sport in state.sports ? config.sport : "football";
            state.league = config.league;
            state.search = config.team_filter;
            state.status = config.match_status;
            state.date = config.date;
            el.outputFile.value = config.output_file;
        } catch (error) {
            state.league = state.sports[state.sport].leagues[0].id;
            toast(`Using defaults: ${error.message}`, "error");
        }
        renderSportTabs();
        populateLeagues();
        syncControls();
        loadEvents();
        initOracle();
    }

    init();
})();
