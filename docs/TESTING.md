# Testing FeedVault

How to run the checks, what each one covers, and the rules that keep them
from touching real data or real tools. The short version is in the
[README](../README.md#development-and-tests).

All commands run from the repository root unless a step says `cd frontend`.
`./run.sh` creates `backend/venv` and installs the Python dependencies on
its first run; `npm ci` (or `npm install`) in `frontend/` installs the UI's.

## Safety rules

These hold for people and for coding agents alike.

- **Never run the real download tools from a test.** Not instaloader,
  gallery-dl or yt-dlp, not their `--help`, not an import of their Python
  packages. The tests use the fakes in `backend/tests/` (below), and the
  guard fails any test that reaches anything else.
- **Never test deleting on real data.** Trash, purge and Empty trash are
  tried on the demo vault or a temp directory only, never on your own media
  folders.
- **Never loosen a security rule to make a test pass**: the `X-FeedVault`
  header, the Host allowlist, the frame rules, the media rules (see the top
  of [API.md](API.md#security-rules)), the backend test guard or the browser
  test harness's refusals.
- **Keep the browser tests away from the real app.** They run their own
  instance on a free port; they never use ports 3380 (the live app) or 3389
  (`./testapp.sh`), your `~/.config/feedvault` or `~/.cache/feedvault-demo`.

## Backend tests

```bash
backend/venv/bin/python -m pytest -q backend/tests
# or
./run.sh --test
```

About 1,900 tests (some 800 test functions, many of them parametrized) in
`backend/tests/`; the whole run takes about 5 minutes on a 12-core laptop. pytest is the only extra dependency
(`backend/requirements-dev.txt`). Each test gets an isolated config,
database and media root from the `env` fixture, and a Flask test client
from `client` (`backend/tests/conftest.py`).

### The tool guard

`backend/tests/toolguard.py`, installed for every test by the autouse
`tool_guard` fixture in `conftest.py` (#56). For each test:

- `PATH` is an empty folder of the test's own, and `HOME`, the
  `XDG_*` folders and the temp dir are folders beside the test's tmp dir.
  `PIPX_HOME`, `VIRTUAL_ENV`, `DISPLAY`, `DBUS_SESSION_BUS_ADDRESS` and
  the like are unset.
- Starting a program (`subprocess.Popen`, `os.exec*`, `os.spawn*`,
  `posix_spawn`, `os.system`) is refused unless its real path is inside the
  test's tmp dir or `backend/tests/` (the fakes), or it is this Python
  (`/bin/sh` is let through only as the `#!` of a script a test wrote). A
  script's `#!` interpreter is checked too, and so is a script run from a
  memfd (how FeedVault runs a user's shell script).
- `shutil.which` and `jobs.tool_path` (which includes a tool path set in
  Settings) are refused when they find something outside those folders.
- Importing `instaloader`, `gallery_dl`, `yt_dlp`, `pip` or `pipx` from
  anywhere else is refused.
- A connection or name lookup to anything but this machine is refused.
- Every Python a test starts runs under the same guard
  (`backend/tests/guard_site/sitecustomize.py` on `PYTHONPATH`), so a fake or
  a script cannot get around it.

A refusal raises `ToolGuardError` and is written to a log; the test then
fails at teardown even if the code caught the error. A refusal outside any
test (from a thread a test left running) fails the whole run.

The fakes:

- `fakes.py` writes instaloader, gallery-dl and yt-dlp **output** (posts,
  JSON, media) with the standard library. `scripts/make_demo.py` uses it too.
- `fake_instaloader.py` and `fake_downloaders.py` (gallery-dl, yt-dlp) are
  stand-in programs for the flags FeedVault passes. They read what to
  "download", or which failure to act out (rate limit, login, private, not
  found), from a JSON file named in an environment variable, and never touch
  the network. Tests install them as `#!<this Python>` scripts in a `bin/`
  folder in their tmp dir, put first on `PATH`.

A test must never reach a real tool, a real `HOME` or the network. The
guard has a `tool_guard.allow(path, why)` escape hatch (see `conftest.py`);
do not add one to get around a refusal: give the test a fake instead.

`backend/tests/test_api_doc.py` checks that [API.md](API.md) lists every
route of the app's URL map and nothing else (see the top of API.md for the
row format it reads).

## Frontend checks

```bash
cd frontend
npm ci              # once
npx eslint .        # or: npm run lint
npm test            # Node unit tests (node --test)
npm run build       # Vite build into frontend/dist
```

`npm test` runs the files listed in `package.json`'s `test` script (all in
`frontend/scripts/`): formatting helpers, themes, source options, account
health, the userscript, the Vite dev server's settings (its proxy follows
`FEEDVAULT_PORT`), the review queue, the browser test harness and shards
(below), the port checks of `run.sh` and `testapp.sh` and `testapp.sh`'s
test data checks, reset and copy (each block runs alone in bash, against a
tmp dir with a fake `HOME` and live config), and the `--help` of `run.sh`
and `testapp.sh` (the only way either is started). A new `*.test.js` file has
to be added to that list.

## Browser tests

```bash
cd frontend
npm run build                         # the backend serves frontend/dist
npx playwright install chromium       # once
npm run e2e                           # every project
npm run e2e -- --project=themes       # one project
projects=$(node e2e/shards.js 3) && npm run e2e -- $projects   # one CI shard
```

Playwright (`frontend/playwright.config.js`, specs in `frontend/e2e/`)
against a throwaway FeedVault that `e2e/global-setup.js` starts once for the
run and deletes afterwards. Tests run one at a time on that one instance:
about 130 tests, 4 to 5 minutes for a full local run.

### What the harness isolates and refuses

`frontend/e2e/harness.js`:

- Everything lives in a fresh `$TMPDIR/feedvault-e2e-*` folder: the demo
  vault that `scripts/make_demo.py --stress` builds there (invented posts and
  fake gallery-dl, yt-dlp and instaloader in its `bin/`), its config and
  database, and a `HOME`, XDG folders, `TMPDIR` and an empty `PATH` of its
  own. Schedules are paused.
- The backend and the demo generator run under the backend tests' guard (see
  [The tool guard](#the-tool-guard)); the harness first checks that the guard
  is in force, and fails the run if the guard refused anything.
- Every tool in the instance's config must be one of the vault's fakes.
- It refuses ports 3380 and 3389, a port something already listens on, and a
  config or folder inside (or containing) `~/.config/feedvault`,
  `$XDG_CONFIG_HOME/feedvault`, `~/.cache/feedvault-demo`,
  `$XDG_CACHE_HOME/feedvault-demo` or `$FEEDVAULT_DEMO_DIR`. Before running
  tests, it checks that the server answering on its port reports this
  vault's data directory.
- When the run ends, the backend is stopped by its PID (SIGTERM, then SIGKILL
  after 10 s), any process of the run's left in the folder is stopped, the
  backend log is copied to `frontend/test-results/backend.log`, and the
  folder is deleted. A process counts as the run's only if its working
  folder or command line is in the folder and its environment
  (`/proc/<pid>/environ`) holds the run's `FEEDVAULT_TEST_GUARD`: a shell
  `cd`'d there, or a `tail -f` of the backend log, is left alone.

#### An interrupted run

A run cut short (Ctrl+C, `kill`, SIGKILL, a CI cancel, a crash) leaves
nothing running, and its folder goes at the latest at the next run (Linux):

- The backend starts through `e2e/die-with-runner.py`, which sets
  `PR_SET_PDEATHSIG` and then execs it: the kernel sends it SIGTERM when the
  Playwright runner dies, however it dies. SIGTERM is the backend's usual
  shutdown, which stops the jobs it started in sessions of their own.
- If the runner exits before teardown (setup failed or was interrupted, or it
  exits mid-run), its exit stops the backend (SIGTERM, up to 5 s, then
  SIGKILL), stops the run's processes left, and deletes the folder.
- Each run writes `owner.pid` in its folder (the runner's PID, its start time
  and the boot id, so a reused PID does not count, and the run's guard). Each
  start then deletes this user's `feedvault-e2e-*` folders whose runner is
  gone, after stopping by PID what still runs there with that run's guard. A
  folder whose runner is alive (another checkout's run) is left alone, and so
  is one with no `owner.pid` (from before this change): delete that by hand
  once no run uses it.

Environment variables:

| Variable | What it does |
|---|---|
| `FEEDVAULT_E2E_PYTHON` | The Python to run the backend with (needs `backend/requirements.txt`). Default: `backend/venv/bin/python`, else `python3` on `PATH`. CI sets it to setup-python's |
| `FEEDVAULT_E2E_PORT` | A fixed port instead of a free one. 3380, 3389 or a busy port is refused |
| `FEEDVAULT_E2E_SHOTS` | A folder: `layout` writes a screenshot of each view there, to compare by eye before and after a change. Not a baseline |

`fixtures.js` fails any test that logs a console error or a page error, or
gets an `/api` response of 400 or more. A test can let one expected error
through with `pageErrors.allow(...)`.

### Projects

| Project | Spec | Viewport | Checks |
|---|---|---|---|
| `desktop` | `desktop.spec.js`, `links.spec.js` | 1440×900 | Every page loads without errors, the Feed shows posts, Review keeps, trashes and undoes from the keyboard; saved links added, edited, reordered and deleted, a link's whole notes in view when editing, Tab out of a picker |
| `layout` | `layout.spec.js` | 1024×768, 1280×800, 1440×900, 1920×1080 | 22 views and 7 dialogs: nothing overlaps, no text is clipped without a title, nothing is off screen, no sideways scroll, click targets of at least 24×24; every page title sits in the same place; the person page's and Links page's parts line up (1280 to 1920) |
| `layout-zoom` | `layout.spec.js` | 1152×720 at 1.25 device pixels (1440×900 at 125% zoom) | The same layout rules (the title test is skipped) |
| `states` | `states.spec.js` | 1280×800, 1440×900 | Focus rings (and Tab never dropping focus on `<body>`) on every page in the nav, a post and a person, hover, popovers, scrolled pages, jumps landing below the header |
| `themes` | `themes.spec.js` | 1440×900 | WCAG AA text contrast in each of the 8 preset themes |
| `phone` | `phone.spec.js`, `safe-area.spec.js` | 375×812 with touch (iPhone X, in Chromium) | No sideways scroll, the nav drawer, Review's bar, folded Feed filters, tables as cards, notch and home bar insets |

### Shards

CI runs the projects in 4 shards, one job each (`frontend/e2e/shards.js`):

| Shard | Projects |
|---|---|
| 1 | `layout` |
| 2 | `states` |
| 3 | `layout-zoom`, `themes` |
| 4 | `desktop`, `phone` |

`node e2e/shards.js <n>` prints shard n's `--project=…` arguments, and
fails for a shard that does not exist. Keep it a step of its own, as above
and in CI: inside `npm run e2e -- $(…)` a failure leaves no arguments, and
every project runs. Locally, `npm run e2e` runs every
project in one go.

To add a project:

1. Add it to `projects` in `playwright.config.js`.
2. Add it to a shard in `SHARDS` in `e2e/shards.js` (or add a shard).
3. If the number of shards changed, update the `e2e-shard` job in
   `.github/workflows/ci.yml`: its `matrix.shard` list and the `/4` in its
   name.

`npm test` (`scripts/e2eShards.test.js`) fails until every project is in
exactly one shard and `ci.yml` matches the number of shards.

### No pixel baselines

The tests check geometry and computed styles (overlap, clipping, contrast),
never screenshots against a baseline (`toHaveScreenshot`). The UI uses the
system font (`system-ui`), and CI's Ubuntu runner falls back to DejaVu Sans,
which is wider and has a shorter line than the Noto Sans of a typical
desktop, so pixels would never match. A layout check can still pass locally
and fail in CI because text wraps differently.

To run the browser tests with CI's font, point fontconfig at a config that
only has DejaVu Sans (installed on most Linux systems as
`fonts-dejavu-core`):

```bash
cat > /tmp/fonts-ci.conf <<'EOF'
<?xml version="1.0"?>
<!DOCTYPE fontconfig SYSTEM "fonts.dtd">
<fontconfig>
  <dir>/usr/share/fonts/truetype/dejavu</dir>
  <cachedir>/tmp/fonts-ci-cache</cachedir>
  <include ignore_missing="yes">/etc/fonts/conf.d</include>
</fontconfig>
EOF
FONTCONFIG_FILE=/tmp/fonts-ci.conf fc-match system-ui   # should say DejaVu Sans
cd frontend && FONTCONFIG_FILE=/tmp/fonts-ci.conf npm run e2e
```

`/usr/share/fonts/truetype/dejavu` is Debian's and Ubuntu's folder; on
other systems use the one `fc-list | grep DejaVuSans.ttf` shows. Chromium
inherits `FONTCONFIG_FILE` from the test runner; nothing else changes.

## Performance

### A large vault

`scripts/make_demo.py --large N` adds N invented posts to the demo vault:
about 300 accounts over Instagram, Twitter/X, TikTok and YouTube, 50
people, 40 tags, 15 collections, 300 links, copies in typo'd folders,
reposts (some resized), stray files and 300 posts in the trash. Every
picture is a small PNG of random noise, every name made up. It keeps the
demo's safety: the root must be empty or already carry the demo's marker,
and nothing is downloaded or run (the downloaders are the fakes).

```bash
backend/venv/bin/python scripts/make_demo.py --large 20000 /tmp/fv-large
```

Use a tmp dir of your own, never `~/.cache/feedvault-demo`, and delete it
afterwards (20,000 posts take about 450 MB with the index).

### Measuring

```bash
cd frontend && npm run build
FEEDVAULT_E2E_PYTHON=../backend/venv/bin/python npm run perf -- --posts 20000 [--runs 5] [--json out.json]
```

`e2e/perf.js` starts the browser tests' throwaway instance (the same tmp
dir, test guard and free port as `npm run e2e`) on `make_demo.py --large N`
and prints Markdown tables:

- the index: built from nothing (cold) and rescanned with nothing changed
  (warm, `POST /api/scan`);
- the hashing worker: its first pass over every file, with four calls the
  pages make timed while it runs and once it is idle, and the pass the
  warm rescan starts;
- about 40 API calls the pages make: *uncached* right after a write, which
  drops the backend's memo caches as a sync or review does, and *cached*,
  the same call again; the median of `--runs`;
- 15 pages in Chromium: navigation to the last API response and the last
  DOM change after it (interactive), the median of 3;
- the long list pages (Storage, Unmatched, Links, Creators): the rows in
  the DOM of those the page lists, the DOM's elements, interactive, and one
  thing a user does there (sort, or type in the search box): the slowest
  input's event handlers (React's render included) and its time to the
  next paint (Event Timing, as INP reads it; headless Chromium adds some
  40-60 ms to any input, whatever the page), then the time until the page
  is quiet again; the median of 3.

`--lists-only` measures the list pages alone (no API table, no other page).
It stops the backend by its PID and deletes the tmp dir however it ends.

### Numbers

20,000 invented posts (19.5k indexed, 35.9k media files, 1.06k unmatched,
309 accounts, 50 people), on a desktop with an SSD. Before is `main` at
79b3691, after is the large-vault performance pass.

| Measure | before | after |
|---|---:|---:|
| Index, cold | 10.77 s | 6.40 s |
| Index, warm rescan | 6.84 s | 2.17 s |
| `/api/duplicates?kind=similar`, uncached | 670 ms | 83 ms |
| `/api/duplicates?kind=content`, uncached | 174 ms | 41 ms |
| `/api/duplicates?kind=copies`, uncached | 90 ms | 21 ms |
| `/api/storage`, uncached | 92 ms | 84 ms |
| `/api/sources`, uncached | 84 ms | 72 ms |
| `/api/stats?person=…` | 58 ms | 48 ms |
| `/api/new`, uncached | 44 ms | 38 ms |
| `/api/people`, `/api/authors`, uncached | 38, 40 ms | 34, 34 ms |
| `/api/posts?limit=60&offset=15000` | 32 ms | 25 ms |
| `/api/posts?…&q=moss` | 16 ms | 13 ms |
| Every other call, uncached | ≤ 18 ms | ≤ 15 ms |
| Every call, cached (except stats) | ≤ 29 ms | ≤ 24 ms |

| Page, interactive | before | after |
|---|---:|---:|
| Feed | 193 ms | 192 ms |
| Feed, search | 331 ms | 317 ms |
| Review | 159 ms | 139 ms |
| Creators | 285 ms | 276 ms |
| Person | 199 ms | 188 ms |
| Storage | 277 ms | 212 ms |
| Stats | 106 ms | 114 ms |
| Duplicates | 204 ms | 129 ms |
| Trash | 127 ms | 149 ms |
| Tags | 100 ms | 94 ms |
| Collections | 104 ms | 94 ms |
| Collection | 111 ms | 105 ms |
| Links | 141 ms | 131 ms |
| Unmatched | 165 ms | 160 ms |
| Jobs | 110 ms | 94 ms |

The first `similar` after the pictures change still compares them all
(about 370 ms here); later ones reuse the pairs.

### Hashing

Hashing never blocked a request or a scan: it is a background thread that
starts once the scan is done and commits in short batches. What it cost the
dashboard was Python's one-thread-at-a-time lock: while its threads
decoded pictures (the dHash phase, most of the first pass), every request
waited for that lock each time it read a row. `perf.js` times four calls
back to back while the first pass runs. Before is `main` at 739d9da, after
is the background-hashing pass. Same vault as above.

| Measure | before | after |
|---|---:|---:|
| `/api/posts?limit=60` while hashing, median (idle: 3 ms) | 17 ms | 4 ms |
| `/api/posts?limit=60` while hashing, slowest | 33 ms | 20 ms |
| `/api/duplicates?kind=content` while hashing, slowest (idle: 4 ms) | 634 ms | 100 ms |
| `/api/stats` while hashing, median (idle: 21 ms) | 25 ms | 22 ms |
| Pass after a rescan that changed nothing | 1.40 s | 0.69 s |
| First pass, dashboard idle (36k files) | 21.1 s | 17.4 s |
| of which the dHash phase | 14.7 s | 9.4 s |
| First pass, the dashboard asking without pause | 13.8 s | 20.5 s |

- The worker waits before each file while a request is being answered (at
  most a second per file, so a dashboard that never stops asking slows
  the pass down without stopping it: the last row). Finding which videos to
  measure, which compares every picture with every other at the loosest
  threshold (1.8 s here, after every scan that brings new pictures), waits
  the same way between buckets of pictures.
- Each phase stats its files first and keeps only the new or changed ones
  (by size and mtime), so a pass that has nothing to do opens no file, and
  the Duplicates page's "Fingerprinting pictures 1,200 / 7,500" counts
  only what is left.
- The picture threads each take the next file when free; they used to take
  four at a time and wait for the slowest (an ffmpeg frame) before the
  next four.

The slowest calls left during a pass are the first ones after each of the
worker's commits (every 10 s), which drop the memo caches as any write
does. "Dashboard idle" is a script that runs `hashing.run_pass` on a copy
of the vault with nothing else running.

### The long list pages

Storage's creator table, Unmatched, Links and Creators' grids rendered
every row at once. They now render the rows near the viewport
(`frontend/src/lib/windowing.js`, about 200 lines, no dependency: the
page still scrolls as a whole, so a list library built around a box of
its own would not fit, and the grids need a column count read from the
CSS). A list shorter than 120 rows renders whole, as before. Same vault;
before is `main` at 4f5a549:

| List page | rows in the DOM | DOM elements | interactive | interaction | handlers | input to paint |
|---|---:|---:|---:|---|---:|---:|
| Storage, before | 309 of 309 | 12,120 | 225 ms | sort by posts, twice | 34 ms | 216 ms |
| Storage, after | 22 of 309 | 2,303 | 195 ms | the same | 13 ms | 112 ms |
| Unmatched, before | 1,064 of 1,064 | 5,673 | 172 ms | no search box | | |
| Unmatched, after | 18 of 1,064 | 268 | 130 ms | type "img_001" in its search | 33 ms | 144 ms |
| Links, before | 305 of 305 | 5,768 | 113 ms | type "link 1" in its search | 9 ms | 72 ms |
| Links, after | 18 of 305 | 805 | 118 ms | the same | 2 ms | 64 ms |
| Creators, before | 262 of 262 | 5,044 | 328 ms | type "ma" in its filter, clear it | 39 ms | 104 ms |
| Creators, after | 53 of 262 | 1,206 | 294 ms | the same | 22 ms | 80 ms |

Storage's sort was the slow one: 34 ms of React, then some 180 ms of
style, layout and paint over 12k elements. Links was fine already (its
search goes to the backend), and is windowed for the vaults whose links
run to thousands. Creators' interactive is its five API calls, not its
cards. The APIs return every row still: `/api/unmatched` is 5 ms and
242 KiB for 1,064 files, `/api/links` 4 ms, so paging them would add
round trips for nothing; the search boxes filter what is loaded
(Storage, Unmatched, Creators) or ask the backend (Links).

What a windowed list keeps:

- **Search instead of Ctrl+F.** The browser's find bar sees the rendered
  rows only. Every windowed page has a search box over all its rows:
  Storage's creator table and Unmatched got one (path and reason), Links
  and Creators had theirs.
- **Keyboard focus.** The line holding focus is always rendered, so focus
  never falls to `<body>` when its row scrolls away; Tab and Shift+Tab go
  row to row past the viewport (the lines just beyond it are rendered,
  and the browser scrolls each focused one into view). Leaving for a
  dialog keeps the row: the dialog gives focus back (`restoreFocus`).
- **Unsaved edits.** The link being edited on Links is always rendered:
  its form holds the edits `lib/unsaved.js` guards.
- **Links to one row.** Links' "saved already" and Creators' `?source=`
  (a failed sync's notification) scroll to their row with
  `scrollTo(index)`: straight to about where it is, then centred once it
  is rendered and measured.
- **Entry animations** play on the first rows only: rows mounted while
  scrolling come in as they are.

### Regression tests

`backend/tests/test_perf.py` runs in the backend suite without timing
anything:

- the hot requests run the same number of SQL statements on an archive
  four times as large, and a feed page of 10 posts the same as one of 200;
- a page of post summaries read in batch equals what one post at a time
  gave;
- the content-duplicate query's plan uses its index (migration 22) and does
  not scan `media_hash`;
- similar pairs are computed once for the same pictures;
- a hashing pass after a rescan that changed nothing opens no file and
  announces nothing to do; one rewritten picture is read once per phase;
  an interrupted pass keeps what it read and the next reads only the rest;
- the hashing worker reads no file while a request is being answered (the
  app counts each request once), one slow file holds up only its own
  thread, and finding the videos to measure pauses as often as its
  buckets say;
- instaloader's parser goes over a folder's names a fixed number of times,
  and its name index matches what its old pattern matched.

`frontend/e2e/lists.spec.js` (desktop project) gives each list page
thousands of invented rows (the real answer plus `page.route`, nothing
written) and counts what is in the DOM: fewer than 120 rows, while the
last row is reached by scrolling, the search finds any row, sorting covers
them all, Tab walks past the first viewport, focus stays on a row scrolled
away, an edit in progress survives scrolling, and a 409 or `?source=`
brings a row that was never rendered into view, lit. On `main` each test
fails at its first count. `frontend/scripts/windowing.test.js` (`npm
test`) checks the window's arithmetic: which lines render for a scroll
position, and that the spacers add up to the rows they stand for.

## CI

`.github/workflows/ci.yml`, on every push to `main` and every pull request
(Ubuntu 22.04, Python 3.12, Node 20):

| Job | Runs |
|---|---|
| `backend` | `pip install -r backend/requirements-dev.txt`, then `python -m pytest -q backend/tests` |
| `frontend` | `npm ci`, `npm run lint`, `npm test`, `npm run build` |
| `e2e shard 1/4` … `4/4` | Builds the UI, installs Chromium's headless shell, then `npm run e2e -- $(node e2e/shards.js <n>)` with `FEEDVAULT_E2E_PYTHON` set. Each shard has its own runner and instance; one failing does not stop the others. On failure, the Playwright report, traces and the backend log are uploaded as `playwright-report-shard-<n>` |
| `e2e` | The one e2e result for the PR: green only if every shard passed |

In CI, Playwright retries a failed test once; locally it does not.
