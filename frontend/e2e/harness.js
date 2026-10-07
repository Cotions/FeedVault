// The throwaway FeedVault the browser tests run against (npm run e2e).
//
// A fresh tmp dir holds everything: the demo vault scripts/make_demo.py
// builds (invented posts, fake gallery-dl, yt-dlp and instaloader in its
// bin/), its config and database, and a HOME, XDG folders, TMPDIR and an
// empty PATH of its own, so neither the generator nor the backend can see
// the user's ~/.config/feedvault, ~/.cache/feedvault-demo or a real tool.
// Both run under the backend tests' guard (#56, backend/tests/toolguard.py,
// through guard_site on PYTHONPATH): starting a program outside the tmp dir
// and the fakes (this Python aside), importing a downloader's package or
// reaching the network fails, and any refusal fails the run at teardown.
//
// The backend listens on a free port, never the live app's (3380) or
// testapp.sh's (3389), and is stopped by its PID; the tmp dir is deleted.
//
// A run that is cut short (#110) cleans up too: the backend gets SIGTERM
// when the runner dies, however it dies (die-with-runner.py); a runner
// that exits during setup or the tests stops it and deletes the tmp dir
// on its way out; and each start sweeps the tmp dirs of runs whose runner
// is gone (owner.pid), killing only what carries that run's guard.
//
// No Playwright import here: the Node tests (npm test) check the refusals.
import { spawn, execFile } from "node:child_process";
import fs from "node:fs";
import net from "node:net";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

export const REPO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
const TESTS = path.join(REPO, "backend", "tests");
export const RESERVED_PORTS = new Set([3380, 3389]);   // the live app, testapp.sh
const TMP_PREFIX = "feedvault-e2e-";
const READY_MS = 90_000;
const STOP_MS = 10_000;
const EXIT_STOP_MS = 5_000;                   // the backend's shutdown, on the runner's way out
const OWNER = "owner.pid";
const LAUNCHER = path.join(REPO, "frontend", "e2e", "die-with-runner.py");
const PROC = fs.existsSync("/proc/self/stat");   // Linux: processes can be checked

// Folders the instance must never be in: the user's real config and demo.
export function protectedDirs(env = process.env, home = os.homedir()) {
  const configHome = env.XDG_CONFIG_HOME || path.join(home, ".config");
  const cacheHome = env.XDG_CACHE_HOME || path.join(home, ".cache");
  return [...new Set([
    path.join(home, ".config", "feedvault"),
    path.join(configHome, "feedvault"),
    path.join(home, ".cache", "feedvault-demo"),
    path.join(cacheHome, "feedvault-demo"),
    env.FEEDVAULT_DEMO_DIR,                   // testapp.sh --demo's, moved
  ].filter(Boolean).map(p => path.resolve(p)))];
}

function real(p) {
  // The deepest part that exists, resolved, with the rest appended: a
  // symlink above a path not made yet still counts.
  let head = path.resolve(p);
  const rest = [];
  while (!fs.existsSync(head)) {
    const up = path.dirname(head);
    if (up === head) break;
    rest.unshift(path.basename(head));
    head = up;
  }
  try { head = fs.realpathSync(head); } catch { /* left as it is */ }
  return path.join(head, ...rest);
}

function within(p, dir) {
  const rel = path.relative(real(dir), real(p));
  return rel === "" || (!rel.startsWith("..") && !path.isAbsolute(rel));
}

// Throws unless the port and the folders are safe to use.
export function checkSafe({ port, configPath, root }, env = process.env, home = os.homedir()) {
  if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error(`e2e: bad port ${port}`);
  if (RESERVED_PORTS.has(port)) throw new Error(`e2e: refusing port ${port} (3380 is the live app, 3389 testapp.sh's)`);
  // The live app or testapp.sh moved: their ports are refused too, running or not.
  for (const name of ["FEEDVAULT_PORT", "FEEDVAULT_TEST_PORT"]) {
    if (/^[0-9]{1,5}$/.test(env[name] || "") && Number(env[name]) === port) {
      throw new Error(`e2e: refusing port ${port} (${name}, the live app's or testapp.sh's)`);
    }
  }
  for (const p of [configPath, root].filter(Boolean)) {
    for (const dir of protectedDirs(env, home)) {
      if (within(p, dir) || within(dir, p)) throw new Error(`e2e: refusing ${p}: it touches ${dir}`);
    }
  }
}

// Whether something already listens on ``port``: another FeedVault on a
// moved port (FEEDVAULT_PORT, FEEDVAULT_TEST_PORT) is refused that way.
export function portBusy(port) {
  return new Promise(resolve => {
    const sock = net.connect({ port, host: "127.0.0.1" });
    sock.once("connect", () => { sock.destroy(); resolve(true); });
    sock.once("error", () => resolve(false));
  });
}

// FEEDVAULT_E2E_PORT when set (checked, never ignored), else a free port.
export async function pickPort(env = process.env, free = freePort) {
  if (env.FEEDVAULT_E2E_PORT === undefined || env.FEEDVAULT_E2E_PORT === "") {
    // The OS's pick may be a reserved port (FEEDVAULT_PORT in the ephemeral
    // range, its app down): another one, a few times.
    for (let i = 0; i < 20; i++) {
      const port = await free();
      try {
        checkSafe({ port }, env);
        return port;
      } catch { /* reserved: next */ }
    }
    throw new Error("e2e: no free port that is not reserved");
  }
  const port = /^\d+$/.test(env.FEEDVAULT_E2E_PORT) ? Number(env.FEEDVAULT_E2E_PORT) : NaN;
  checkSafe({ port }, env);
  return port;
}

export function freePort() {
  return new Promise((resolve, reject) => {
    const srv = net.createServer();
    srv.unref();
    srv.on("error", reject);
    srv.listen(0, "127.0.0.1", () => {
      const { port } = srv.address();
      srv.close(() => resolve(port));
    });
  });
}

// The Python with the backend's requirements: FEEDVAULT_E2E_PYTHON, else the
// backend's virtualenv, else python3 on PATH (CI's setup-python). An
// absolute path: the instance's own PATH is empty.
export function findPython(env = process.env) {
  if (env.FEEDVAULT_E2E_PYTHON) return path.resolve(env.FEEDVAULT_E2E_PYTHON);
  const venv = path.join(REPO, "backend", "venv", "bin", "python");
  if (fs.existsSync(venv)) return venv;
  for (const dir of (env.PATH || "").split(path.delimiter)) {
    const p = path.join(dir, "python3");
    if (path.isAbsolute(dir) && fs.existsSync(p)) return p;
  }
  throw new Error("e2e: no Python found (set FEEDVAULT_E2E_PYTHON)");
}

function isolatedEnv(root, python) {
  const home = path.join(root, "home");
  const guard = {
    roots: [fs.realpathSync(root), fs.realpathSync(TESTS)],
    allowed: { [fs.realpathSync(python)]: "this Python: the backend, and every fake's shebang" },
    interpreters: {},
    log: path.join(root, "guard.log"),
  };
  return {
    HOME: home,
    XDG_CONFIG_HOME: path.join(home, ".config"),
    XDG_DATA_HOME: path.join(home, ".local", "share"),
    XDG_STATE_HOME: path.join(home, ".local", "state"),
    XDG_CACHE_HOME: path.join(home, ".cache"),
    TMPDIR: path.join(root, "tmp"),
    PATH: path.join(root, "path"),            // empty: no tool, no ffmpeg found by name
    LANG: "C.UTF-8",
    PYTHONDONTWRITEBYTECODE: "1",
    PYTHONPATH: path.join(TESTS, "guard_site"),
    FEEDVAULT_TEST_GUARD: JSON.stringify(guard),
  };
}

function run(cmd, args, opts) {
  return new Promise((resolve, reject) => {
    execFile(cmd, args, { ...opts, maxBuffer: 16 << 20 }, (err, stdout, stderr) => {
      if (err) reject(new Error(`${path.basename(args[0] || cmd)} failed: ${err.message}\n${stdout}${stderr}`));
      else resolve(stdout);
    });
  });
}

// The guard must be in force in the Pythons started with ``env``: one of
// them tries to start /bin/true, which is outside the guard's roots, and
// must be refused (into a log of its own, not the run's).
async function probeGuard(python, env, root) {
  const guard = { ...JSON.parse(env.FEEDVAULT_TEST_GUARD), log: path.join(root, "probe.log") };
  const code = "import subprocess\ntry:\n    subprocess.run(['/bin/true'])\nexcept Exception as e:\n    print(type(e).__name__)\n";
  const out = await run(python, ["-c", code], { env: { ...env, FEEDVAULT_TEST_GUARD: JSON.stringify(guard) }, cwd: root });
  if (out.trim() !== "ToolGuardError") throw new Error(`e2e: the test guard is not in force (${out.trim() || "/bin/true ran"})`);
  fs.rmSync(guard.log, { force: true });
}

async function ask(base, url) {
  const r = await fetch(base + url, { headers: { "X-FeedVault": "1" }, signal: AbortSignal.timeout(5000) });
  if (!r.ok) throw new Error(`${url}: HTTP ${r.status}`);
  return r.json();
}

// The kernel's start time of ``pid`` (clock ticks since boot, a string),
// or null when there is no such process (or only its zombie): with the
// boot's id, what tells a runner from a later process given its PID.
export function startTime(pid) {
  try {
    const stat = fs.readFileSync(`/proc/${pid}/stat`, "utf8");
    // Field 22; the name (field 2) may hold spaces and parentheses.
    const after = stat.slice(stat.lastIndexOf(")") + 2).split(" ");
    return after[0] === "Z" ? null : after[19];
  } catch {
    return null;
  }
}

function bootId() {
  try { return fs.readFileSync("/proc/sys/kernel/random/boot_id", "utf8").trim(); } catch { return ""; }
}

// A run's owner.pid: {pid, start, boot, guard}, or null when it is missing
// or not one this harness wrote for ``root`` (its guard names ``root``).
export function readOwner(root) {
  try {
    const o = JSON.parse(fs.readFileSync(path.join(root, OWNER), "utf8"));
    const ok = Number.isInteger(o.pid) && o.pid > 0 && typeof o.start === "string" && o.start !== ""
      && typeof o.boot === "string" && typeof o.guard === "string"
      && JSON.parse(o.guard).roots?.[0] === fs.realpathSync(root);
    return ok ? o : null;
  } catch {
    return null;
  }
}

export function ownerAlive(owner) {
  return owner.boot === bootId() && startTime(owner.pid) === owner.start;
}

// Deletes this user's tmp dirs (``feedvault-e2e-*`` in ``tmp``) whose
// runner is gone, after killing, by PID, what still runs in them with
// their run's guard (pidsIn). A dir whose runner is alive (another
// checkout's run, say), or with no valid owner.pid (made before #110, or
// at this very instant), is left alone. Returns the dirs deleted.
export function sweepStale({ tmp = os.tmpdir(), log = () => {} } = {}) {
  if (!PROC) return [];                       // no telling a live runner from a dead one
  let names;
  try { names = fs.readdirSync(tmp); } catch { return []; }
  const swept = [];
  for (const name of names) {
    if (!name.startsWith(TMP_PREFIX)) continue;
    const dir = path.join(tmp, name);
    let st;
    try { st = fs.lstatSync(dir); } catch { continue; }
    if (!st.isDirectory() || st.uid !== process.getuid()) continue;   // a link, a file, someone else's
    const owner = readOwner(dir);
    if (!owner) {
      log(`e2e: ${dir} has no owner.pid: left alone (delete it by hand once no run uses it)`);
      continue;
    }
    if (ownerAlive(owner)) continue;
    for (const pid of pidsIn(dir, owner.guard)) {
      try { process.kill(pid, "SIGKILL"); } catch { /* gone already */ }
    }
    const still = pidsIn(dir, owner.guard);
    if (still.length) {
      log(`e2e: ${dir}: its runner is gone, but pids ${still.join(", ")} still run there: left`);
      continue;
    }
    fs.rmSync(dir, { recursive: true, force: true });
    log(`e2e: deleted ${dir}, left by an interrupted run (runner pid ${owner.pid})`);
    swept.push(dir);
  }
  return swept;
}

// Sweeps the dead runs' tmp dirs, then makes this run's, owned by this
// process (owner.pid), with the isolated environment for ``python``. Until
// stopInstance takes over, this process's exit stops the backend and
// deletes the dir (exitCleanup): a setup that throws or is interrupted
// leaves nothing behind.
export function openRun({ python, tmp = os.tmpdir(), log = () => {} }) {
  sweepStale({ tmp, log });
  const root = fs.mkdtempSync(path.join(tmp, TMP_PREFIX));
  const inst = { root, guard: null, env: null, pid: null, proc: null, url: null, logFile: path.join(root, "backend.log") };
  try {
    inst.env = isolatedEnv(root, python);
    inst.guard = inst.env.FEEDVAULT_TEST_GUARD;
    const owner = { pid: process.pid, start: startTime(process.pid) || "", boot: bootId(), guard: inst.guard };
    fs.writeFileSync(path.join(root, OWNER), JSON.stringify(owner), { flag: "wx", mode: 0o600 });
  } catch (e) {
    fs.rmSync(root, { recursive: true, force: true });
    throw e;
  }
  inst.onExit = () => exitCleanup(inst);
  process.on("exit", inst.onExit);
  return inst;
}

// Starts ``python args`` as the run's backend, in the tmp dir with the
// run's environment, through die-with-runner.py (Linux): it gets SIGTERM
// if this process dies without stopping it.
export function launchBackend(inst, python, args, { env = {}, stdio = "ignore" } = {}) {
  const argv = PROC ? [LAUNCHER, String(process.pid), python, ...args] : args;
  inst.proc = spawn(python, argv, { cwd: inst.root, env: { ...inst.env, ...env }, stdio });
  inst.pid = inst.proc.pid;
  return inst.proc;
}

function running(inst) {
  return Boolean(inst.pid && inst.proc && inst.proc.exitCode === null && inst.proc.signalCode === null);
}

// On this process's way out with the run not stopped (a setup that threw
// or was interrupted, a runner that exits mid-run), synchronously: SIGTERM
// to the backend and up to EXIT_STOP_MS for its shutdown (it stops its
// jobs), SIGKILL after; SIGKILL to the run's processes left; the tmp dir
// deleted once nothing runs there (else the next start's sweep does it).
export function exitCleanup(inst) {
  if (running(inst)) {
    try { process.kill(inst.pid, "SIGTERM"); } catch { /* gone already */ }
    const nap = new Int32Array(new SharedArrayBuffer(4));
    const until = Date.now() + EXIT_STOP_MS;
    while (startTime(inst.pid) !== null && Date.now() < until) Atomics.wait(nap, 0, 0, 50);
    if (startTime(inst.pid) !== null) {
      try { process.kill(inst.pid, "SIGKILL"); } catch { /* gone already */ }
    }
  }
  for (const pid of pidsIn(inst.root, inst.guard)) {
    try { process.kill(pid, "SIGKILL"); } catch { /* gone already */ }
  }
  if (!pidsIn(inst.root, inst.guard).length) fs.rmSync(inst.root, { recursive: true, force: true });
}

// Builds the demo vault and starts the backend; resolves to the instance.
// ``stress``: the demo with make_demo.py's worst cases for the layout
// checks (--stress), in this throwaway vault only.
export async function startInstance({ log = () => {}, stress = false } = {}) {
  const port = await pickPort();
  checkSafe({ port });                        // before anything is made
  if (await portBusy(port)) throw new Error(`e2e: refusing port ${port}: something already listens there`);
  const dist = path.join(REPO, "frontend", "dist", "index.html");
  if (!fs.existsSync(dist)) throw new Error("e2e: the UI is not built (npm run build)");
  const python = findPython();
  const inst = openRun({ python, log });
  const { root, env } = inst;
  try {
    const vault = path.join(root, "vault");
    const configPath = path.join(vault, "config.json");
    checkSafe({ port, configPath, root });
    for (const d of [env.XDG_CONFIG_HOME, env.XDG_DATA_HOME, env.XDG_STATE_HOME, env.XDG_CACHE_HOME, env.TMPDIR, env.PATH]) {
      fs.mkdirSync(d, { recursive: true, mode: 0o700 });
    }

    await probeGuard(python, env, root);
    log(`e2e: building the demo vault in ${vault}`);
    const demoArgs = [path.join(REPO, "scripts", "make_demo.py"), ...(stress ? ["--stress"] : []), vault];
    await run(python, demoArgs, { env, cwd: root });
    const cfg = JSON.parse(fs.readFileSync(configPath, "utf8"));
    // Every tool the instance knows is one of the demo's fakes.
    for (const [tool, exe] of Object.entries(cfg.tools || {})) {
      if (!within(exe, path.join(vault, "bin"))) throw new Error(`e2e: tool ${tool} is not a fake: ${exe}`);
    }
    // No scheduled sync starts while the tests run: the pages hold still.
    cfg.schedules_paused = true;
    fs.writeFileSync(configPath, JSON.stringify(cfg, null, 2));

    const out = fs.openSync(inst.logFile, "a");
    // Not detached: a Ctrl+C reaches it too. What it starts in sessions of
    // its own is found by stopInstance.
    launchBackend(inst, python, [path.join(REPO, "backend", "app.py")], {
      env: { FEEDVAULT_CONFIG: configPath, FEEDVAULT_PORT: String(port), FEEDVAULT_NO_BROWSER: "1" },
      stdio: ["ignore", out, out],
    });
    fs.closeSync(out);
    inst.url = `http://127.0.0.1:${port}`;
    log(`e2e: backend pid ${inst.pid} on ${inst.url}`);
    await waitReady(inst);
    // The server that answers is this one, not another on the same port.
    const answered = (await ask(inst.url, "/api/config")).data_directory;
    if (!answered || !within(answered, vault) || !within(vault, path.dirname(answered))) {
      throw new Error(`e2e: the server on ${inst.url} is not this instance (data in ${answered})`);
    }
    return inst;
  } catch (e) {
    const tail = fs.existsSync(inst.logFile) ? fs.readFileSync(inst.logFile, "utf8").slice(-4000) : "";
    await stopInstance(inst).catch(() => {});
    throw tail ? new Error(`${e.message}\n--- backend log ---\n${tail}`) : e;
  }
}

// Up, and its first scan over (a scan ending refreshes the open page).
async function waitReady(inst) {
  const until = Date.now() + READY_MS;
  let last;
  while (Date.now() < until) {
    if (inst.proc.exitCode !== null || inst.proc.signalCode !== null) throw new Error("e2e: the backend exited");
    try {
      await ask(inst.url, "/api/stats");
      const scan = await ask(inst.url, "/api/scan");
      if (!scan.running) return;
      last = new Error("still scanning");
    } catch (e) {
      last = e;
    }
    await new Promise(r => setTimeout(r, 250));
  }
  throw new Error(`e2e: the backend was not ready within ${READY_MS / 1000} s (${last?.message})`);
}

// PIDs of this user's processes whose working folder or command line is in
// ``root`` and whose environment holds ``guard`` as its
// FEEDVAULT_TEST_GUARD (Linux's /proc; none elsewhere): what the run's
// backend started in sessions of their own (jobs, tool probes) and left
// behind. The guard names the run's tmp dir, so a shell cd'd there or a
// tail -f of its log is not the run's; nor is a process whose environment
// cannot be read.
export function pidsIn(root, guard) {
  if (!guard) return [];
  let entries;
  try { entries = fs.readdirSync("/proc"); } catch { return []; }
  let realRoot = root;
  try { realRoot = fs.realpathSync(root); } catch { /* deleted: as given */ }
  const inRoot = x => [root, realRoot].some(r => x === r || x.startsWith(r + path.sep));
  const mark = `FEEDVAULT_TEST_GUARD=${guard}`;
  const out = [];
  for (const e of entries) {
    if (!/^\d+$/.test(e) || Number(e) === process.pid) continue;
    try {
      const cwd = fs.readlinkSync(`/proc/${e}/cwd`);
      const cmd = fs.readFileSync(`/proc/${e}/cmdline`, "utf8").split("\0");
      if (!(inRoot(cwd) || cmd.some(inRoot))) continue;
      if (fs.readFileSync(`/proc/${e}/environ`, "utf8").split("\0").includes(mark)) out.push(Number(e));
    } catch { /* gone, or not ours (its environment unreadable) */ }
  }
  return out;
}

// Stops the backend by its PID (SIGTERM, then SIGKILL), then anything of
// the run's left running in the tmp dir (pidsIn), by PID; checks the guard's log, and deletes the
// tmp dir. ``saveLog``: where to copy the backend's log first.
export async function stopInstance(inst, { saveLog } = {}) {
  if (!inst) return;
  const { pid, root } = inst;
  if (pid && inst.proc.exitCode === null && inst.proc.signalCode === null) {
    const exited = new Promise(r => inst.proc.once("exit", r));
    try { process.kill(pid, "SIGTERM"); } catch { /* gone already */ }
    const timer = new Promise(r => setTimeout(r, STOP_MS, "timeout"));
    if (await Promise.race([exited, timer]) === "timeout") {
      try { process.kill(pid, "SIGKILL"); } catch { /* gone already */ }
      await exited;
    }
  }
  if (pid && inst.proc.exitCode === null && inst.proc.signalCode === null) {
    throw new Error(`e2e: backend pid ${pid} is still running`);
  }
  if (root) {
    for (const left of pidsIn(root, inst.guard)) {
      try { process.kill(left, "SIGKILL"); } catch { /* gone already */ }
    }
    const still = pidsIn(root, inst.guard);
    if (still.length) throw new Error(`e2e: still running in ${root}: pids ${still.join(", ")}`);
  }
  let violations = "";
  if (root && path.basename(root).startsWith(TMP_PREFIX) && within(root, os.tmpdir())) {
    const guardLog = path.join(root, "guard.log");
    if (fs.existsSync(guardLog)) violations = fs.readFileSync(guardLog, "utf8").trim();
    if (saveLog && fs.existsSync(inst.logFile)) {
      fs.mkdirSync(path.dirname(saveLog), { recursive: true });
      fs.copyFileSync(inst.logFile, saveLog);
    }
    fs.rmSync(root, { recursive: true, force: true });
  }
  if (inst.onExit) process.off("exit", inst.onExit);   // stopped: nothing left for the way out
  if (violations) throw new Error(`e2e: the test guard refused:\n${violations}`);
}
