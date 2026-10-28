import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Routes, Route, Link, NavLink, useLocation, useNavigate, useParams } from "react-router-dom";
import { getScan, startScan, getJobs, getNotifications, getProfiles, quitApp, onConnectionChange } from "./lib/api";
import { ScanContext } from "./lib/scan";
import { JobsContext, ENDED, SAVE_KINDS, endTone, shownParams } from "./lib/jobs";
import { ToastContext } from "./lib/toast";
import { ProfilesContext } from "./lib/profiles";
import { fmtAgo, fmtInt, plural } from "./lib/fmt";
import { SETUP_ERRORS, SYNC_KINDS, batchKey } from "./lib/sources";
import { personPath } from "./lib/people";
import { SearchContext, scopeText, searchScope } from "./lib/search";
import { droppedUrl, mayCarryUrl } from "./lib/links";
import { desktopAllowed, notificationPath } from "./lib/notify";
import { FOCUSABLE, PHONE } from "./lib/layout";
import Icon            from "./components/Icon";
import CyberBackground from "./components/CyberBackground";
import Notifications   from "./components/Notifications";
import ProfileSwitch   from "./components/ProfileSwitch";
import ScrollManager   from "./components/ScrollManager";
import QuickAddLink, { UNSORTED_PATH } from "./components/QuickAddLink";
import Feed            from "./pages/Feed";
import PostPage        from "./pages/PostPage";
import Review          from "./pages/Review";
import Creators        from "./pages/Creators";
import PersonPage      from "./pages/PersonPage";
import Tags            from "./pages/Tags";
import Links           from "./pages/Links";
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
// started from elsewhere), and slower again in a hidden tab. With the Jobs
// page open, a job started elsewhere (the scheduler, another tab) shows
// within a few seconds, not on the idle poll (#139).
const JOBS_POLL_MS        = 1000;
const JOBS_HIDDEN_POLL_MS = 5000;
const JOBS_IDLE_POLL_MS   = 15000;
const JOBS_PAGE_POLL_MS   = 3000;
// A URL dragged over the window: the drop overlay goes when the drag leaves,
// ends, or (a drag cancelled outside the window tells nothing) when no
// dragover came for this long.
const DRAG_GONE_MS        = 600;
// An idle jobs poll that brought nothing new still re-renders this often.
const JOBS_SAME_MS        = 60000;
// Desktop notifications on: a hidden tab still polls, now and then.
const DESKTOP_POLL_MS     = 60000;
const DESKTOP_MAX         = 5;               // notifications shown at once; the bell lists the rest
// How long a toast shows: one with an action (Undo) long enough to reach
// it, one with a link a while, an error longer than news. The time stops
// while the pointer is on the toast or focus is in it, and once it leaves
// there are at least TOAST_RESUME_MS left.
const TOAST_MS        = { action: 15000, link: 9000, err: 7000, ok: 4000 };
const TOAST_RESUME_MS = 2000;

/* A page about one entity (a person, a collection, a post), mounted afresh
   for each: going from one to the next in the app (a link between two
   people, Back, Forward) keeps nothing typed or opened for the first, so a
   save always goes to the entity whose text it is. */
function PerEntity({ page: Page }) {
  const params = useParams();
  return <Page key={JSON.stringify(params)} />;
}

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
  const [profiles, setProfiles] = useState([]);
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
  // On a phone the sidebar is a drawer behind the header's menu button.
  const [drawer, setDrawer] = useState(false);
  const menuRef   = useRef(null);
  const navRef    = useRef(null);
  const headerRef = useRef(null);
  const mainRef   = useRef(null);
  // Quick-add a link (#165): { url, clipboard, n } while open; n remounts it for another drop.
  const [quickAdd, setQuickAdd] = useState(null);
  const [dropping, setDropping] = useState(false);   // a URL is dragged over the window

  const toast = useCallback((text, kind = "ok", link) => {
    const id = ++toastId.current;
    const ms = link?.onClick ? TOAST_MS.action : link ? TOAST_MS.link : kind === "err" ? TOAST_MS.err : TOAST_MS.ok;
    setToasts(list => [...list.slice(-3), { id, text, kind, link, ms }]);
  }, []);
  const dismissToast = useCallback(id => setToasts(list => list.filter(t => t.id !== id)), []);

  // The search box searches the page it is on when that page searches itself
  // (lib/search.js: the Feed, Creators, a person, Links), in that page's `q`
  // query parameter, so reload and Back both restore a search; anywhere else
  // it drives the Feed's. The input keeps its own state and pushes to the URL
  // after a short pause: binding it straight to the URL lost keystrokes,
  // because router navigations commit in a transition and the controlled input
  // re-rendered with the stale value in between.
  const scope  = searchScope(location.pathname);
  const urlQ   = scope ? new URLSearchParams(location.search).get("q") || "" : "";
  const [query,      setQuery]      = useState(urlQ);
  const [seenUrlQ,   setSeenUrlQ]   = useState(urlQ);
  const [lastPushed, setLastPushed] = useState(urlQ);
  const [searchName, setSearchName] = useState(null);    // the person whose page it searches
  // The URL changed under us (Back, a hashtag link, another page): adopt it,
  // unless it is just our own push landing.
  if (urlQ !== seenUrlQ) {
    setSeenUrlQ(urlQ);
    if (urlQ !== lastPushed) { setQuery(urlQ); setLastPushed(urlQ); }
  }

  useEffect(() => {
    if (query === urlQ) return;
    const t = setTimeout(() => {
      const params = new URLSearchParams(scope ? location.search : "");
      if (query) params.set("q", query); else params.delete("q");
      const search = params.toString();
      setLastPushed(query);
      // Typing on a page that searches itself replaces its entry; typing
      // elsewhere opens the feed.
      navigate({ pathname: scope ? location.pathname : "/", search: search ? `?${search}` : "" }, { replace: !!scope });
    }, scope ? 200 : 0);
    return () => clearTimeout(t);
  }, [query, urlQ, scope, location.pathname, location.search, navigate]);

  // A page that refused the search (Links' "Keep editing") puts the box back.
  const urlQRef = useRef(urlQ);
  useEffect(() => { urlQRef.current = urlQ; }, [urlQ]);
  const searchCtx = useMemo(() => ({
    setName: setSearchName,
    reset: () => { setQuery(urlQRef.current); setLastPushed(urlQRef.current); },
  }), [setQuery, setLastPushed]);
  const searchText = scopeText(scope, searchName);

  // The phone's drawer closes first: it traps Tab and makes the rest inert.
  // Opened again while open (Alt+L), it stays as it is, typing and all; a
  // dropped address starts it afresh with that address.
  const openQuickAdd = useCallback((url = "") => {
    setDrawer(false);
    setQuickAdd(q => (q && !url ? q : { url, clipboard: !url, n: (q?.n ?? 0) + 1 }));
  }, []);
  const closeQuickAdd = useCallback(() => setQuickAdd(null), []);

  // "/" and Ctrl/Cmd-K jump to search from anywhere, Esc drops focus.
  // Alt+L opens the quick-add, but not while typing (Alt+L may type a character).
  useEffect(() => {
    function onKey(e) {
      const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName) || e.target.isContentEditable;
      const hot = e.key === "k" && (e.metaKey || e.ctrlKey);
      if (e.code === "KeyL" && e.altKey && !e.ctrlKey && !e.metaKey && !e.shiftKey && !typing && !e.repeat) {
        e.preventDefault();
        openQuickAdd();
      } else if (hot || (e.key === "/" && !typing)) {
        e.preventDefault();
        searchRef.current?.focus();
        searchRef.current?.select();
      } else if (e.key === "Escape" && e.target === searchRef.current) {
        // On a page that searches itself, Esc first drops the search: the
        // page as it is without one. The Feed's stays, as it always has.
        const own = searchScope(window.location.pathname);
        if (own && !own.feed && searchRef.current.value) setQuery("");
        else searchRef.current.blur();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [openQuickAdd]);

  // A web address dropped anywhere opens the quick-add with it. Only a drag
  // from outside the page: one that started here (reordering collections,
  // an image) fires dragstart first, and is left to the page. So is a drop
  // on a text field (it types the text there, as always). A link
  // (text/uri-list) shows the drop overlay; plain text cannot be read
  // before the drop, so it shows none, and opens the quick-add only if it
  // turns out to be an address (anything else is let go, unremarked).
  useEffect(() => {
    let inside = false, timer = null, insideTimer = null;
    const editable = t => t instanceof Element && (t.closest("input, textarea, select") || t.isContentEditable);
    const ours = e => !inside && mayCarryUrl(e.dataTransfer?.types) && !editable(e.target);
    const isLink = e => [...(e.dataTransfer?.types || [])].includes("text/uri-list");
    const gone = () => { clearTimeout(timer); setDropping(false); };
    // The page's own drag is over at dragend, or, should that never come
    // (its node went away mid-drag), once its drag events stop.
    const outside = () => { inside = false; clearTimeout(insideTimer); };
    const stillInside = () => { clearTimeout(insideTimer); insideTimer = setTimeout(outside, DRAG_GONE_MS); };
    function onStart() { inside = true; stillInside(); }
    function onEnd() { outside(); gone(); }
    function onOver(e) {
      if (inside) { stillInside(); return; }
      if (!ours(e)) { if (editable(e.target)) gone(); return; }
      e.preventDefault();
      e.dataTransfer.dropEffect = "copy";
      if (!isLink(e)) return;
      setDropping(true);
      clearTimeout(timer);
      timer = setTimeout(gone, DRAG_GONE_MS);
    }
    function onLeave(e) { if (!e.relatedTarget) gone(); }      // out of the window
    function onDrop(e) {
      const mine = ours(e);
      outside();
      gone();
      if (!mine) return;
      e.preventDefault();                       // not a browser's own (Firefox opens a dropped link)
      const url = droppedUrl(type => e.dataTransfer.getData(type));
      if (url) openQuickAdd(url);
    }
    const on = { dragstart: onStart, dragend: onEnd, dragenter: onOver, dragover: onOver, dragleave: onLeave, drop: onDrop };
    for (const [ev, fn] of Object.entries(on)) window.addEventListener(ev, fn);
    return () => {
      clearTimeout(timer);
      clearTimeout(insideTimer);
      for (const [ev, fn] of Object.entries(on)) window.removeEventListener(ev, fn);
    };
  }, [openQuickAdd]);

  // The drawer closes when a link in it is followed (onClick on the nav, even
  // to the page already shown), when the page changes (Back), and when the
  // screen grows past the breakpoint. Not on every new history entry: search
  // and filters replace the URL on a timer, which must not shut it.
  useEffect(() => {
    const mq = window.matchMedia(PHONE);
    const onChange = () => { if (!mq.matches) setDrawer(false); };
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);
  const [navPath, setNavPath] = useState(location.pathname);
  if (location.pathname !== navPath) { setNavPath(location.pathname); setDrawer(false); }

  // While the drawer is open: focus moves into it and Tab stays there, Escape
  // closes it (after the bell's panel or the quit confirmation, which close
  // first), the header and the page are inert, and the page does not scroll.
  // Closing disarms Quit and gives focus back to the menu button.
  useEffect(() => {
    if (!drawer) return;
    const nav = navRef.current;
    const focusable = () => [...nav.querySelectorAll(FOCUSABLE)];
    (nav.querySelector(".side-link.active") || focusable()[0])?.focus();
    function onKey(e) {
      if (e.key === "Escape") {
        if (!nav.querySelector(".notif-panel, .side-quit-confirm")) setDrawer(false);
      } else if (e.key === "Tab") {
        const list = focusable();
        const first = list[0], last = list[list.length - 1];
        if (!nav.contains(document.activeElement)) { e.preventDefault(); first?.focus(); }
        else if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
        else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
      }
    }
    const html = document.documentElement.style;
    const overflow = html.overflow;
    html.overflow = "hidden";
    // The attribute, not React's prop: React 18 and 19 disagree on its value.
    const behind = [headerRef.current, mainRef.current].filter(Boolean);
    for (const el of behind) el.setAttribute("inert", "");
    document.addEventListener("keydown", onKey);
    const button = menuRef.current;
    return () => {
      document.removeEventListener("keydown", onKey);
      html.overflow = overflow;
      for (const el of behind) el.removeAttribute("inert");
      setConfirmQuit(false);
      if (button && document.contains(button)) button.focus();
    };
  }, [drawer]);

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

  // A poll's answer replaces the state only when it differs from the last
  // one: an identical answer (the usual idle poll) would re-render the whole
  // app, the page under it included, for nothing. While a job is active, and
  // once a minute otherwise, the jobs answer is applied anyway: the "2m ago"
  // and "starts in 40 s" the pages show move on with those renders.
  const lastScan = useRef("");
  const lastJobs = useRef({ text: "", at: 0 });
  const applyScan = useCallback(s => {
    const text = JSON.stringify(s);
    if (text !== lastScan.current) { lastScan.current = text; setScan(s); }
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
    const text = JSON.stringify(list);
    const now = Date.now();
    if (text !== lastJobs.current.text || list.running || list.queued || now - lastJobs.current.at >= JOBS_SAME_MS) {
      lastJobs.current = { text, at: now };
      setJobList(list);
    }
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
      else toast(`${j.label}: ${j.message}`, endTone(j));
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

  // Saved: a toast one click from where it went. A page that lists links
  // (Links, a person's) loads them again; the Unsorted badge is read again.
  const quickSaved = useCallback(link => {
    setQuickAdd(null);
    const p = link.people?.[0];
    toast(p ? `Link saved to ${p.name}` : "Link saved to Unsorted", "ok",
      { to: p ? personPath(p.id) : UNSORTED_PATH, label: "Show" });
    if (/^\/(links|people\/)/.test(window.location.pathname)) setRefreshKey(k => k + 1);
    pollJobs();
  }, [toast, pollJobs]);

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
  const onJobsPage  = location.pathname === "/jobs";
  useEffect(() => {
    if (quit || online === false) return;
    const ms = jobsActive ? (visible ? JOBS_POLL_MS : JOBS_HIDDEN_POLL_MS)
      : visible ? (onJobsPage ? JOBS_PAGE_POLL_MS : JOBS_IDLE_POLL_MS) : desktopOn ? DESKTOP_POLL_MS : null;
    if (!ms) return;
    const t = setInterval(pollJobs, ms);
    return () => clearInterval(t);
  }, [jobsActive, visible, onJobsPage, desktopOn, online, quit, pollJobs]);
  // Opening Jobs reads the list at once: what it shows is never 15 s old.
  useEffect(() => { if (onJobsPage) pollJobs(); }, [onJobsPage, pollJobs]);

  // refresh(): the counts the poll carries changed here (a link saved,
  // given to someone, deleted: the Unsorted badge), read them now.
  const jobsCtx = useMemo(
    () => ({ list: jobList, running: jobsRunning, active: jobsActive, newCount: jobList?.new ?? 0, newUntil: jobList?.new_until ?? null,
             unsortedLinks: jobList?.unsorted_links ?? 0, started: jobStarted, refresh: jobStarted }),
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
      lastScan.current = "";
      setScan(s => ({ last: s?.last ?? null, ...s, running: true }));
    }
    return r;
  }, []);

  // The profiles, for the header's switcher and Settings; the answer also
  // tells api.js which one this page was loaded in.
  const loadProfiles = useCallback(() => {
    getProfiles().then(r => setProfiles(r?.profiles || []), () => { /* offline: the status dot says so */ });
  }, []);
  useEffect(() => { loadProfiles(); }, [loadProfiles]);
  const profilesCtx = useMemo(() => ({ profiles, reload: loadProfiles }), [profiles, loadProfiles]);

  const refresh = useCallback(() => setRefreshKey(k => k + 1), []);
  const scanCtx = useMemo(
    () => ({ status: scan, running, start, refreshKey, refresh }),
    [scan, running, start, refreshKey, refresh],
  );

  // The backend exits right after answering, so the response may never land.
  // Either way the app is going down: show the farewell screen regardless.
  async function handleQuit() {
    setConfirmQuit(false);
    setDrawer(false);
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
  const unsorted = jobList?.unsorted_links ?? 0;

  return (
    <ScanContext.Provider value={scanCtx}>
    <JobsContext.Provider value={jobsCtx}>
    <ToastContext.Provider value={toast}>
    <ProfilesContext.Provider value={profilesCtx}>
    <SearchContext.Provider value={searchCtx}>
      <CyberBackground />
      <ScrollManager />
      <header ref={headerRef}>
        <button
          type="button"
          ref={menuRef}
          className="icon-btn nav-menu-btn"
          aria-expanded={drawer}
          aria-controls="main-nav"
          aria-label={menuLabel(jobList?.notifications?.unread ?? 0, jobsActive, jobList?.new ?? 0, running, lastErrors)}
          title="Pages, jobs, notifications"
          onClick={() => setDrawer(true)}
        >
          <Icon name="menu" size={18} />
          {(jobList?.notifications?.unread ?? 0) > 0 ? (
            <span className="nav-menu-badge">{fmtInt(jobList.notifications.unread)}</span>
          ) : (jobsActive > 0 || jobList?.new > 0 || running || lastErrors > 0) && <span className="nav-menu-dot" />}
        </button>
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
            placeholder={searchText.placeholder}
            aria-label={searchText.label}
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
          <ProfileSwitch />
          {jobsActive > 0 && (
            <Link to="/jobs" className="nav-jobs-live" title={`${jobsRunning} running, ${jobsActive - jobsRunning} queued`}
                  aria-label={`Jobs: ${jobsActive} active`}>
              <JobsIcon running={jobsRunning} size={13} />{jobsActive}
            </Link>
          )}
          <span
            className={`status-dot ${online ? "online" : ""}`}
            title={online ? "Backend connected" : online === false ? "Backend offline" : "Connecting…"}
            role="status"
            aria-label={online ? "Backend connected" : online === false ? "Backend offline" : "Connecting"}
          />
        </div>
      </header>

      <div className="app-body">
        {drawer && <div className="nav-backdrop" onClick={() => setDrawer(false)} />}
        <nav className={`sidebar${drawer ? " is-open" : ""}`} id="main-nav" aria-label="Main" ref={navRef}
             onClick={e => { if (e.target.closest("a[href]")) setDrawer(false); }}>
          <div className="side-drawer-head">
            <span className="card-title">Menu</span>
            <button type="button" className="btn-ghost notif-close" onClick={() => setDrawer(false)} aria-label="Close menu">
              <Icon name="close" size={15} />
            </button>
          </div>
          {/* Three groups, each named by a small label (a role="group" for
              screen readers): what to look at, what the library holds, and
              the app itself. Rescan and Quit stay apart at the foot. */}
          <div className="side-group" role="group" aria-labelledby="side-group-browse">
            <span className="side-group-label" id="side-group-browse">Browse</span>
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
            <div className="side-row side-row-split">
              <NavLink to="/links" className="side-link"><Icon name="link" />Links</NavLink>
              <span className="side-row-end">
                {unsorted > 0 && (
                  <Link to={UNSORTED_PATH} className="side-badge side-unsorted"
                        title={`${plural(unsorted, "link")} tied to no one yet: sort them`}
                        aria-label={`Unsorted links: ${fmtInt(unsorted)}`}>
                    {unsorted > 99 ? "99+" : unsorted}
                  </Link>
                )}
                <button type="button" className="side-badge side-add" onClick={() => openQuickAdd()}
                        title="Add a link (Alt+L)" aria-label="Add a link" aria-keyshortcuts="Alt+L">
                  <Icon name="plus" size={11} />Link
                </button>
              </span>
            </div>
            <NavLink to="/collections" className="side-link"><Icon name="bookmark" />Collections</NavLink>
          </div>
          <div className="side-group" role="group" aria-labelledby="side-group-library">
            <span className="side-group-label" id="side-group-library">Library</span>
            <NavLink to="/stats" className="side-link"><Icon name="chart" />Stats</NavLink>
            <NavLink to="/storage" className="side-link"><Icon name="disk" />Storage</NavLink>
            <NavLink to="/trash" className="side-link"><Icon name="trash" />Trash</NavLink>
            <NavLink to="/unmatched" className="side-link"><Icon name="unmatched" />Unmatched</NavLink>
            <NavLink to="/duplicates" className="side-link"><Icon name="copy" />Duplicates</NavLink>
          </div>
          <div className="side-group" role="group" aria-labelledby="side-group-system">
            <span className="side-group-label" id="side-group-system">System</span>
            <NavLink
              to="/jobs"
              className="side-link"
              title={jobsActive ? `${jobsRunning} running, ${jobsActive - jobsRunning} queued` : "Downloads and other jobs"}
            >
              <Icon name="terminal" />Jobs
              {jobsActive > 0 && (
                <span className="side-badge" aria-label={`${jobsActive} job${jobsActive === 1 ? "" : "s"} active`}>
                  <JobsIcon running={jobsRunning} size={11} />{jobsActive}
                </span>
              )}
            </NavLink>
            <Notifications unread={jobList?.notifications?.unread ?? 0} latest={jobList?.notifications?.latest ?? null}
                           onRead={jobStarted} />
            <NavLink to="/scripts" className="side-link" title="Your own download commands and shell scripts"><Icon name="pencil" />Scripts</NavLink>
            <NavLink to="/settings" className="side-link"><Icon name="settings" />Settings</NavLink>
          </div>
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

        <main ref={mainRef}>
          {online === false && (
            <div className="offline-banner" role="alert">
              <Icon name="warn" size={16} />
              <span>
                <strong>Backend offline.</strong> The dashboard cannot reach the FeedVault
                backend. Start it again; this page reconnects on its own.
              </span>
              <button type="button" className="btn-secondary" onClick={poll}>Retry now</button>
            </div>
          )}
          <Routes>
            <Route path="/" element={<Feed />} />
            <Route path="/p/:platform/:postId" element={<PerEntity page={PostPage} />} />
            <Route path="/review" element={<Review />} />
            <Route path="/creators" element={<Creators />} />
            <Route path="/people/:id" element={<PerEntity page={PersonPage} />} />
            <Route path="/tags" element={<Tags />} />
            <Route path="/links" element={<Links />} />
            <Route path="/collections" element={<Collections />} />
            <Route path="/collections/:id" element={<PerEntity page={CollectionView} />} />
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

      {quickAdd && (
        <QuickAddLink key={quickAdd.n} url={quickAdd.url} clipboard={quickAdd.clipboard}
                      onSaved={quickSaved} onClose={closeQuickAdd} />
      )}
      {dropping && (
        <div className="drop-overlay" aria-hidden="true">
          <div className="drop-overlay-card"><Icon name="link" size={22} />Drop to add the link</div>
        </div>
      )}

      <div className="toasts" role="status" aria-live="polite">
        {toasts.map(t => <Toast key={t.id} toast={t} onDismiss={dismissToast} />)}
      </div>
    </SearchContext.Provider>
    </ProfilesContext.Provider>
    </ToastContext.Provider>
    </JobsContext.Provider>
    </ScanContext.Provider>
  );
}

/* One toast. It goes once its time is up (TOAST_MS), a time that stops
   while the pointer is on it or focus is in it, so it can be read and its
   Undo reached. An action that returns a promise (an Undo's request) keeps
   it, its button busy ("Undo…"), until the answer: the action toasts what
   it did, and this one goes. */
function Toast({ toast: t, onDismiss }) {
  const [hover,  setHover]  = useState(false);
  const [focus,  setFocus]  = useState(false);
  const [acting, setActing] = useState(false);
  const left = useRef(t.ms);
  const { id } = t;
  useEffect(() => {
    if (hover || focus || acting) return;
    const from = Date.now();
    const timer = setTimeout(() => onDismiss(id), left.current);
    return () => {
      clearTimeout(timer);
      left.current = Math.max(TOAST_RESUME_MS, left.current - (Date.now() - from));
    };
  }, [hover, focus, acting, id, onDismiss]);

  async function act() {
    const done = t.link.onClick();
    if (done && typeof done.then === "function") {
      setActing(true);
      try { await done; } catch { /* the action toasts its own failure */ }
    }
    onDismiss(id);
  }

  return (
    <div
      className={`toast toast-${t.kind}`}
      onPointerEnter={() => setHover(true)}
      onPointerLeave={() => setHover(false)}
      onFocus={() => setFocus(true)}
      onBlur={e => { if (!e.currentTarget.contains(e.relatedTarget)) setFocus(false); }}
    >
      <Icon name={t.kind === "err" ? "warn" : t.kind === "info" ? "info" : "check"} size={15} />
      <span>{t.text}</span>
      {t.link?.onClick ? (
        <button type="button" className="toast-link" onClick={act} disabled={acting} aria-busy={acting || undefined}>
          {acting ? `${t.link.label}…` : t.link.label}
        </button>
      ) : t.link && (
        <Link to={t.link.to} className="toast-link" onClick={() => onDismiss(id)}>
          {t.link.label}
        </Link>
      )}
      <button type="button" className="del-btn" onClick={() => onDismiss(id)} aria-label="Dismiss">
        <Icon name="close" size={13} />
      </button>
    </div>
  );
}

/* The active jobs' icon: turning while one runs, a still clock while they
   only wait (queued, or pausing between downloads) (#139). */
function JobsIcon({ running, size }) {
  return running > 0
    ? <Icon name="refresh" size={size} className="spin jobs-icon is-running" />
    : <Icon name="clock" size={size} className="jobs-icon" />;
}

/* The menu button's name says what waits behind it, since on a phone its
   badge stands for the bell, the running jobs, the new posts and the scan. */
function menuLabel(unread, jobs, fresh, scanning, scanErrors) {
  const parts = [
    unread > 0 && `${fmtInt(unread)} unread notification${unread === 1 ? "" : "s"}`,
    jobs > 0 && `${jobs} job${jobs === 1 ? "" : "s"} active`,
    fresh > 0 && `${fmtInt(fresh)} new post${fresh === 1 ? "" : "s"}`,
    scanning && "scanning",
    scanErrors > 0 && `last scan: ${scanErrors} error${scanErrors === 1 ? "" : "s"}`,
  ].filter(Boolean);
  return parts.length ? `Menu (${parts.join(", ")})` : "Menu";
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
    toast(`${j.label}: ${j.message}`, endTone(j));
  }
}

/* A post saved from the userscript: one click from it, or from what to fix. */
function saveToast(toast, j) {
  const code = j.params?.shortcode ?? j.params?.id;
  const platform = j.params?.platform ?? "instagram";
  if (j.state === "done" && j.result?.post) {
    toast(`Saved post ${code}`, "ok", { to: `/p/${encodeURIComponent(platform)}/${encodeURIComponent(code)}`, label: "Show" });
  } else if (j.state === "failed" && SETUP_ERRORS.has(j.result?.error)) {
    toast(`Saving ${code} failed: ${j.message}`, "err", { to: j.result.error === "login_required" ? "/settings#sync" : "/settings#downloaders", label: "Settings" });
  } else {
    toast(`${j.label}: ${j.message}`, endTone(j));
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
  // Cancelled syncs never ran (or not to the end): not counted as synced (#126).
  const cancelled = b.cancelled ?? 0;
  const synced = b.total - cancelled;
  const also = cancelled ? `, ${fmtInt(cancelled)} cancelled` : "";
  if (b.added > 0) {
    const from = b.profiles === 1 && b.first ? syncName(b.first.label) : plural(b.profiles, "profile");
    toast(`${plural(b.added, "new post")} from ${from}`, "ok", { to: "/?new=1", label: "Show" });
  } else if (!b.failed) {
    toast(`Synced ${plural(synced, "source")}: no new posts${also}`);
  }
  if (b.failed) toast(`${fmtInt(b.failed)} of ${plural(synced, "sync")} failed${also}`, "err", { to: "/creators", label: "Sources" });
}
