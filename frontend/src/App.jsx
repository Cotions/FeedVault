import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Routes, Route, NavLink, useLocation, useNavigate } from "react-router-dom";
import { getScan, startScan, quitApp, onConnectionChange } from "./lib/api";
import { ScanContext } from "./lib/scan";
import { ToastContext } from "./lib/toast";
import { fmtAgo } from "./lib/fmt";
import Icon            from "./components/Icon";
import CyberBackground from "./components/CyberBackground";
import ScrollManager   from "./components/ScrollManager";
import Feed            from "./pages/Feed";
import PostPage        from "./pages/PostPage";
import Review          from "./pages/Review";
import Creators        from "./pages/Creators";
import Tags            from "./pages/Tags";
import Stats           from "./pages/Stats";
import Storage         from "./pages/Storage";
import Trash           from "./pages/Trash";
import Unmatched       from "./pages/Unmatched";
import Duplicates      from "./pages/Duplicates";
import Settings        from "./pages/Settings";

const SCAN_POLL_MS    = 1500;
const OFFLINE_POLL_MS = 4000;

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

  const toast = useCallback((text, kind = "ok") => {
    const id = ++toastId.current;
    setToasts(list => [...list.slice(-3), { id, text, kind }]);
    setTimeout(() => setToasts(list => list.filter(t => t.id !== id)), kind === "err" ? 7000 : 4000);
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
          <NavLink to="/" end className="side-link"><Icon name="feed" />Feed</NavLink>
          <NavLink to="/review" className="side-link"><Icon name="review" />Review</NavLink>
          <NavLink to="/creators" className="side-link"><Icon name="users" />Creators</NavLink>
          <NavLink to="/tags" className="side-link"><Icon name="tag" />Tags</NavLink>
          <NavLink to="/stats" className="side-link"><Icon name="chart" />Stats</NavLink>
          <NavLink to="/storage" className="side-link"><Icon name="disk" />Storage</NavLink>
          <NavLink to="/trash" className="side-link"><Icon name="trash" />Trash</NavLink>
          <NavLink to="/unmatched" className="side-link"><Icon name="unmatched" />Unmatched</NavLink>
          <NavLink to="/duplicates" className="side-link"><Icon name="copy" />Duplicates</NavLink>
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
            <Route path="/tags" element={<Tags />} />
            <Route path="/stats" element={<Stats />} />
            <Route path="/storage" element={<Storage />} />
            <Route path="/trash" element={<Trash />} />
            <Route path="/unmatched" element={<Unmatched />} />
            <Route path="/duplicates" element={<Duplicates />} />
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
    </ScanContext.Provider>
  );
}
