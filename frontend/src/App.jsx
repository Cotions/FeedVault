import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Routes, Route, Link, NavLink, useLocation, useNavigate } from "react-router-dom";
import { getScan, startScan, getJobs, getNotifications, quitApp, onConnectionChange } from "./lib/api";
import { ScanContext } from "./lib/scan";
import { JobsContext, ENDED, SAVE_KINDS, shownParams } from "./lib/jobs";
import { ToastContext } from "./lib/toast";
import { fmtAgo, fmtInt, plural } from "./lib/fmt";
import { SETUP_ERRORS, SYNC_KINDS, batchKey } from "./lib/sources";
import { personPath } from "./lib/people";
import { desktopAllowed, notificationPath } from "./lib/notify";
import Icon            from "./components/Icon";
import CyberBackground from "./components/CyberBackground";
import Notifications   from "./components/Notifications";
import ScrollManager   from "./components/ScrollManager";
import Feed            from "./pages/Feed";
import PostPage        from "./pages/PostPage";
import Review          from "./pages/Review";
import Creators        from "./pages/Creators";
import PersonPage      from "./pages/PersonPage";
import Tags            from "./pages/Tags";
import Collections     from "./pages/Collections";
import CollectionView  from "./pages/CollectionView";
import Stats           from "./pages/Stats";
import Storage         from "./pages/Storage";
import Trash           from "./pages/Trash";
import Unmatched       from "./pages/Unmatched";
import Duplicates      from "./pages/Duplicates";
import Settings        from "./pages/Settings";
import Jobs            from "./pages/Jobs";
import Scripts         from "./pages/Scripts";

const SCAN_POLL_MS    = 1500;
const OFFLINE_POLL_MS = 4000;
// Jobs: fast while one is queued or running, slow otherwise (a job may be
// started from elsewhere), and slower again in a hidden tab.
const JOBS_POLL_MS        = 1000;
const JOBS_HIDDEN_POLL_MS = 5000;
const JOBS_IDLE_POLL_MS   = 15000;
// Desktop notifications on: a hidden tab still polls, now and then.
const DESKTOP_POLL_MS     = 60000;
const DESKTOP_MAX         = 5;               // notifications shown at once; the bell lists the rest

export default function App() {
  const location = useLocation();
  const navigate = useNavigate();
  const [online,      setOnline]      = useState(null);   // null until the first answer
  const [scan,        setScan]        = useState(null);
  const [refreshKey,  setRefreshKey]  = useState(0);
  const [confirmQuit, setConfirmQuit] = useState(false);
  const [quit,        setQuit]        = useState(false);
  const searchRef  = useRef(null);
  const quitRef    = useRef(null);
  const wasRunning = useRef(false);
  const [toasts, setToasts] = useState([]);
  const toastId = useRef(0);
  const [jobList, setJobList] = useState(null);
  const [visible, setVisible] = useState(() => !document.hidden);
  // Jobs already ended when the dashboard first loaded are not news: only
  // those above that first answer's newest id get a toast, once each, and
  // a "Sync all" batch only if it was not over yet.
  const jobsSeen = useRef(null);         // { since, told: Set, batches: Set } after the first poll
  // The newest notifications entry already known (null before the first
  // poll): only those above it make a desktop notification.
  const notifSeen = useRef(null);
  const desktopRef = useRef(false);       // this tab shows desktop notifications (the setting, and allowed)
  const navigateRef = useRef(navigate);
  useEffect(() => { navigateRef.current = navigate; }, [navigate]);

  const toast = useCallback((text, kind = "ok", link) => {
    const id = ++toastId.current;
    setToasts(list => [...list.slice(-3), { id, text, kind, link }]);
    setTimeout(() => setToasts(list => list.filter(t => t.id !== id)), link ? 9000 : kind === "err" ? 7000 : 4000);
  }, []);

  // The search box drives the Feed's `q` query parameter, so reload and Back
  // both restore a search. The input keeps its own state and pushes to the URL
  // after a short pause: binding it straight to the URL lost keystrokes,
  // because router navigations commit in a transition and the controlled input
  // re-rendered with the stale value in between.
  const onFeed = location.pathname === "/";
  const urlQ   = onFeed ? new URLSearchParams(location.search).get("q") || "" : "";
  const [query,      setQuery]      = useState(urlQ);
  const [seenUrlQ,   setSeenUrlQ]   = useState(urlQ);
  const [lastPushed, setLastPushed] = useState(urlQ);
  // The URL changed under us (Back, a hashtag link, leaving the feed): adopt it,
  // unless it is just our own push landing.
  if (urlQ !== seenUrlQ) {
    setSeenUrlQ(urlQ);
    if (urlQ !== lastPushed) { setQuery(urlQ); setLastPushed(urlQ); }
  }

  useEffect(() => {
    if (query === urlQ) return;
    const t = setTimeout(() => {
      const params = new URLSearchParams(onFeed ? location.search : "");
      if (query) params.set("q", query); else params.delete("q");
      const search = params.toString();
      setLastPushed(query);
      // Typing on the feed replaces the entry; typing elsewhere opens the feed.
      navigate({ pathname: "/", search: search ? `?${search}` : "" }, { replace: onFeed });
    }, onFeed ? 200 : 0);
    return () => clearTimeout(t);
  }, [query, urlQ, onFeed, location.search, navigate]);

  // "/" and Ctrl/Cmd-K jump to search from anywhere, Esc drops focus.
  useEffect(() => {
    function onKey(e) {
      const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName) || e.target.isContentEditable;
      const hot = e.key === "k" && (e.metaKey || e.ctrlKey);
      if (hot || (e.key === "/" && !typing)) {
        e.preventDefault();
        searchRef.current?.focus();
        searchRef.current?.select();
      } else if (e.key === "Escape" && e.target === searchRef.current) {
        searchRef.current.blur();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  useEffect(() => {
    if (!confirmQuit) return;
    quitRef.current?.focus();
    function onKey(e) { if (e.key === "Escape") setConfirmQuit(false); }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [confirmQuit]);

  // Every API call reports reachability. Coming back online refreshes the
  // current view, since whatever it shows was loaded (or failed) while down.
  useEffect(() => {
    let prev = null;
    return onConnectionChange(v => {
      setOnline(v);
      if (v && prev === false) setRefreshKey(k => k + 1);
      prev = v;
    });
  }, []);

  const applyScan = useCallback(s => {
    setScan(s);
    // A scan that was running and now is not has just finished: refresh.
    if (wasRunning.current && !s.running) setRefreshKey(k => k + 1);
    wasRunning.current = !!s.running;
  }, []);
  // Failures need no handling here: the api layer tracks offline state.
  const poll = useCallback(() => getScan().then(applyScan, () => {}), [applyScan]);

  useEffect(() => { getScan().then(applyScan, () => {}); }, [applyScan]);

  // A job that has ended since: toast it, and refresh the pages if it
  // indexed anything. Jobs started elsewhere (and fast ones that end between
  // two polls) count too.
  const applyJobs = useCallback(list => {
    setJobList(list);
    const notes = list.notifications;
    desktopRef.current = !!notes?.desktop && desktopAllowed();
    const known = notifSeen.current;
    notifSeen.current = Math.max(known ?? 0, notes?.latest ?? 0);
    if (known != null && desktopRef.current && (notes?.latest ?? 0) > known
        && (document.hidden || !document.hasFocus())) {
      showDesktop(known, path => navigateRef.current(path));
    }
    const batch = list.sync_all;
    if (!jobsSeen.current) {
      const active = list.jobs.filter(j => !ENDED.has(j.state)).map(j => j.id);
      const since = Math.min(list.jobs[0]?.id ?? 0, ...active.map(id => id - 1));
      jobsSeen.current = { since, told: new Set(), batches: new Set(batch?.done ? [batchKey(batch)] : []) };
    }
    const { since, told, batches } = jobsSeen.current;
    const inBatch = new Set(batch?.jobs || []);
    let changed = false;
    for (const j of list.jobs) {
      if (j.id <= since || told.has(j.id) || !ENDED.has(j.state)) continue;
      told.add(j.id);
      if (j.result?.added || j.result?.updated) changed = true;
      if (inBatch.has(j.id)) continue;            // one summary once the batch is over
      if (SYNC_KINDS.has(j.kind)) { syncToast(toast, j); continue; }
      if (SAVE_KINDS.has(j.kind)) { saveToast(toast, j); continue; }
      // Which tool or source, for jobs that name one ("Done, yt-dlp: 2024.08.06").
      // A source sync names its profile in its label, not its params (an id).
      const what = j.params?.source ? j.label : shownParams(j).join(" ");
      const head = what ? `, ${what}` : "";
      if (j.state === "done") toast(`Done${head}: ${j.message}`);
      else if (j.state === "failed") toast(`Failed${head}: ${j.message}`, "err");
      else toast(`${j.label}: ${j.message}`);
    }
    if (batch?.done && !batches.has(batchKey(batch))) {
      batches.add(batchKey(batch));
      batchToast(toast, batch);
    }
    if (changed) setRefreshKey(k => k + 1);
  }, [toast]);
  // A visible tab has its toasts, a hidden one its desktop notifications: either
  // way notify-send waits (docs/API.md "Notifications").
  const pollJobs = useCallback(() => getJobs(desktopRef.current || !document.hidden).then(applyJobs, () => {}),
    [applyJobs]);
  // Poll now: the job may be over in less than a second.
  const jobStarted = useCallback(() => { pollJobs(); }, [pollJobs]);

  useEffect(() => { pollJobs(); }, [pollJobs]);
  useEffect(() => {
    function onVisibility() {
      setVisible(!document.hidden);
      if (!document.hidden) pollJobs();
    }
    document.addEventListener("visibilitychange", onVisibility);
    return () => document.removeEventListener("visibilitychange", onVisibility);
  }, [pollJobs]);

  const jobsRunning = jobList?.running ?? 0;
  const jobsActive  = jobsRunning + (jobList?.queued ?? 0);
  const desktopOn   = !!jobList?.notifications?.desktop;
  useEffect(() => {
    if (quit || online === false) return;
    const ms = jobsActive ? (visible ? JOBS_POLL_MS : JOBS_HIDDEN_POLL_MS)
      : visible ? JOBS_IDLE_POLL_MS : desktopOn ? DESKTOP_POLL_MS : null;
    if (!ms) return;
    const t = setInterval(pollJobs, ms);
    return () => clearInterval(t);
  }, [jobsActive, visible, desktopOn, online, quit, pollJobs]);

  const jobsCtx = useMemo(
    () => ({ list: jobList, running: jobsRunning, active: jobsActive, newCount: jobList?.new ?? 0, newUntil: jobList?.new_until ?? null, started: jobStarted }),
    [jobList, jobsRunning, jobsActive, jobStarted],
  );

  const running = !!scan?.running;
  useEffect(() => {
    if (quit) return;
    const ms = online === false ? OFFLINE_POLL_MS : running ? SCAN_POLL_MS : null;
    if (!ms) return;
    const t = setInterval(poll, ms);
    return () => clearInterval(t);
  }, [online, running, quit, poll]);

  const start = useCallback(async () => {
    const r = await startScan();
    if (r?.ok || r?.error === "already running") {
      wasRunning.current = true;
      setScan(s => ({ last: s?.last ?? null, ...s, running: true }));
    }
    return r;
  }, []);

  const scanCtx = useMemo(
    () => ({ status: scan, running, start, refreshKey }),
    [scan, running, start, refreshKey],
  );

  // The backend exits right after answering, so the response may never land.
  // Either way the app is going down: show the farewell screen regardless.
  async function handleQuit() {
    setConfirmQuit(false);
    setQuit(true);
    try { await quitApp(); } catch { /* connection dropped as it exited */ }
  }

  if (quit) {
    return (
      <div className="quit-screen">
        <span className="quit-mark"><Icon name="feed" size={26} /></span>
        <h2>FeedVault stopped</h2>
        <p>The backend has stopped. Close this tab, and start the app again when you need it.</p>
      </div>
    );
  }

  const last = scan?.last;
  const lastErrors = last?.errors?.length || 0;

  return (
    <ScanContext.Provider value={scanCtx}>
    <JobsContext.Provider value={jobsCtx}>
    <ToastContext.Provider value={toast}>
      <CyberBackground />
      <ScrollManager />
      <header>
        <div className="brand">
          <span className="brand-mark"><Icon name="feed" size={17} /></span>
          <h1>FeedVault</h1>
        </div>
        <div className="header-search-wrap" role="search">
          <Icon name="search" size={15} className="header-search-icon" />
          <input
            ref={searchRef}
            type="text"
            className="header-search"
            placeholder="Search captions, authors, tag:name…"
            aria-label="Search posts"
            value={query}
            onChange={e => setQuery(e.target.value)}
          />
          {query ? (
            <button type="button" className="search-clear" onClick={() => setQuery("")} title="Clear search" aria-label="Clear search">
              <Icon name="close" size={14} />
            </button>
          ) : (
            <kbd className="search-kbd">/</kbd>
          )}
        </div>
        <div className="header-right">
          <span
            className={`status-dot ${online ? "online" : ""}`}
            title={online ? "Backend connected" : online === false ? "Backend offline" : "Connecting…"}
            role="status"
            aria-label={online ? "Backend connected" : online === false ? "Backend offline" : "Connecting"}
          />
        </div>
      </header>

      <div className="app-body">
        <nav className="sidebar" aria-label="Main">
          <div className="side-row">
            <NavLink to="/" end className="side-link"><Icon name="feed" />Feed</NavLink>
            {jobList?.new > 0 && (
              <Link to="/?new=1" className="side-badge side-new" title="Posts indexed since you last marked everything seen">
                {fmtInt(jobList.new)} new
              </Link>
            )}
          </div>
          <NavLink to="/review" className="side-link"><Icon name="review" />Review</NavLink>
          <NavLink to="/creators" className={({ isActive }) => `side-link${isActive || location.pathname.startsWith("/people/") ? " active" : ""}`}><Icon name="users" />Creators</NavLink>
          <NavLink to="/tags" className="side-link"><Icon name="tag" />Tags</NavLink>
          <NavLink to="/collections" className="side-link"><Icon name="bookmark" />Collections</NavLink>
          <NavLink to="/stats" className="side-link"><Icon name="chart" />Stats</NavLink>
          <NavLink to="/storage" className="side-link"><Icon name="disk" />Storage</NavLink>
          <NavLink to="/trash" className="side-link"><Icon name="trash" />Trash</NavLink>
          <NavLink to="/unmatched" className="side-link"><Icon name="unmatched" />Unmatched</NavLink>
          <NavLink to="/duplicates" className="side-link"><Icon name="copy" />Duplicates</NavLink>
          <NavLink
            to="/jobs"
            className="side-link"
            title={jobsActive ? `${jobsRunning} running, ${jobsActive - jobsRunning} queued` : "Downloads and other jobs"}
          >
            <Icon name="terminal" />Jobs
            {jobsActive > 0 && (
              <span className="side-badge" aria-label={`${jobsActive} job${jobsActive === 1 ? "" : "s"} active`}>
                <Icon name="refresh" size={11} className="spin" />{jobsActive}
              </span>
            )}
          </NavLink>
          <Notifications unread={jobList?.notifications?.unread ?? 0} latest={jobList?.notifications?.latest ?? null}
                         onRead={jobStarted} />
          <NavLink to="/scripts" className="side-link" title="Your own download commands and shell scripts"><Icon name="pencil" />Scripts</NavLink>
          <NavLink to="/settings" className="side-link"><Icon name="settings" />Settings</NavLink>
          <div className="side-sep" />
          <button
            type="button"
            className={`side-link side-scan${running ? " is-running" : ""}`}
            onClick={start}
            disabled={running || online === false}
            title="Rescan the media folders"
          >
            <Icon name="refresh" className={running ? "spin" : ""} />
            {running ? "Scanning…" : "Rescan"}
          </button>
          <div className="side-scan-status" aria-live="polite">
            {running ? (
              <span className="scan-live">reading folders…</span>
            ) : last ? (
              <>
                <span title={last.finished_at ? new Date(last.finished_at * 1000).toLocaleString() : ""}>
                  {fmtAgo(last.finished_at)}
                </span>
                <span className="scan-delta">+{last.added ?? 0} ~{last.updated ?? 0}</span>
                {lastErrors > 0 && (
                  <NavLink to="/settings" className="scan-errors" title="See scan errors in Settings">
                    {lastErrors} err
                  </NavLink>
                )}
              </>
            ) : online === false ? (
              <span>offline</span>
            ) : (
              <span>no scan yet</span>
            )}
          </div>
          <div className="side-sep" />
          {confirmQuit ? (
            <div className="side-quit-confirm">
              <span className="del-confirm-label">Quit FeedVault?</span>
              <button type="button" ref={quitRef} className="del-btn del-btn-confirm" onClick={handleQuit} title="Confirm quit" aria-label="Confirm quit">
                <Icon name="check" />
              </button>
              <button type="button" className="del-btn" onClick={() => setConfirmQuit(false)} title="Cancel (Esc)" aria-label="Cancel quit">
                <Icon name="close" />
              </button>
            </div>
          ) : (
            <button type="button" className="side-link side-link-quit" onClick={() => setConfirmQuit(true)} title="Stop the backend and close the app">
              <Icon name="power" />Quit
            </button>
          )}
        </nav>

        <main>
          {online === false && (
            <div className="offline-banner" role="alert">
              <Icon name="warn" size={16} />
              <span>
                <strong>Backend offline.</strong> The dashboard cannot reach FeedVault on
                {" "}<code>127.0.0.1:3380</code>. Start it again; this page reconnects on its own.
              </span>
              <button type="button" className="btn-secondary" onClick={poll}>Retry now</button>
            </div>
          )}
          <Routes>
            <Route path="/" element={<Feed />} />
            <Route path="/p/:platform/:postId" element={<PostPage />} />
            <Route path="/review" element={<Review />} />
            <Route path="/creators" element={<Creators />} />
            <Route path="/people/:id" element={<PersonPage />} />
            <Route path="/tags" element={<Tags />} />
            <Route path="/collections" element={<Collections />} />
            <Route path="/collections/:id" element={<CollectionView />} />
            <Route path="/stats" element={<Stats />} />
            <Route path="/storage" element={<Storage />} />
            <Route path="/trash" element={<Trash />} />
            <Route path="/unmatched" element={<Unmatched />} />
            <Route path="/duplicates" element={<Duplicates />} />
            <Route path="/jobs" element={<Jobs />} />
            <Route path="/scripts" element={<Scripts />} />
            <Route path="/settings" element={<Settings />} />
            <Route path="*" element={<div className="card"><div className="empty">Nothing here.</div></div>} />
          </Routes>
        </main>
      </div>

      <div className="toasts" role="status" aria-live="polite">
        {toasts.map(t => (
          <div key={t.id} className={`toast toast-${t.kind}`}>
            <Icon name={t.kind === "err" ? "warn" : "check"} size={15} />
            <span>{t.text}</span>
            {t.link && (
              <Link to={t.link.to} className="toast-link" onClick={() => setToasts(list => list.filter(x => x.id !== t.id))}>
                {t.link.label}
              </Link>
            )}
            <button
              type="button"
              className="del-btn"
              onClick={() => setToasts(list => list.filter(x => x.id !== t.id))}
              aria-label="Dismiss"
            >
              <Icon name="close" size={13} />
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
    </JobsContext.Provider>
    </ScanContext.Provider>
  );
}

// "Sync @name" / "Sync x.com/name" → "@name" / "x.com/name"
const syncName = label => label.replace(/^Sync /, "");

/* A sync that ended on its own (not part of "Sync all"): what came in, one
   click from the Feed filtered to it, or what failed, one click from its
   source. */
function syncToast(toast, j) {
  if (j.result?.muted) return;                    // muted: no toast (docs/API.md "New posts")
  const added = j.result?.added || 0;
  const who = syncName(j.label);
  if (j.state === "failed") {
    const to = j.result?.person ? personPath(j.result.person) : "/creators";
    toast(`Sync of ${who} failed: ${j.message}`, "err", { to, label: "Source" });
  } else if (j.state === "done" && added > 0) {
    // Exactly the posts it brought: its notifications entry (notify.py).
    const a = j.result.account;
    const to = j.result.notification ? `/?notification=${j.result.notification}`
      : `/?${new URLSearchParams({ new: "1", ...(a ? { platform: a.platform, author: a.id } : {}) })}`;
    toast(`${plural(added, "new post")} from ${who}`, "ok", { to, label: "Show" });
  } else if (j.state === "done") {
    toast(`${who}: no new posts`);
  } else {
    toast(`${j.label}: ${j.message}`);
  }
}

/* A post saved from the userscript: one click from it, or from what to fix. */
function saveToast(toast, j) {
  const code = j.params?.shortcode ?? j.params?.id;
  const platform = j.params?.platform ?? "instagram";
  if (j.state === "done" && j.result?.post) {
    toast(`Saved post ${code}`, "ok", { to: `/p/${encodeURIComponent(platform)}/${encodeURIComponent(code)}`, label: "Show" });
  } else if (j.state === "failed" && SETUP_ERRORS.has(j.result?.error)) {
    toast(`Saving ${code} failed: ${j.message}`, "err", { to: "/settings#downloaders", label: "Settings" });
  } else {
    toast(`${j.label}: ${j.message}`, j.state === "failed" ? "err" : undefined);
  }
}

/* Desktop notifications for the entries above `after` (unread ones, the
   newest DESKTOP_MAX): the browser's Notification API, text only. A click
   opens what the entry leads to. */
function showDesktop(after, go) {
  getNotifications().then(r => {
    const fresh = r.entries.filter(e => e.id > after && !e.read).slice(0, DESKTOP_MAX).reverse();
    for (const e of fresh) {
      if (!desktopAllowed()) return;
      const note = new Notification("FeedVault", { body: e.text, tag: `feedvault-${e.id}` });
      note.onclick = () => { window.focus(); go(notificationPath(e)); note.close(); };
    }
  }, () => {});
}

// "Sync all" is one toast when it is over (and one more if any failed).
function batchToast(toast, b) {
  if (b.added > 0) {
    const from = b.profiles === 1 && b.first ? syncName(b.first.label) : plural(b.profiles, "profile");
    toast(`${plural(b.added, "new post")} from ${from}`, "ok", { to: "/?new=1", label: "Show" });
  } else if (!b.failed) {
    toast(`Synced ${plural(b.total, "source")}: no new posts`);
  }
  if (b.failed) toast(`${fmtInt(b.failed)} of ${plural(b.total, "sync")} failed`, "err", { to: "/creators", label: "Sources" });
}
