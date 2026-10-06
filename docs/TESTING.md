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
health, the userscript, the Vite dev server's settings, the review queue,
and the browser test harness and shards (below). A new `*.test.js` file has
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
  after 10 s), any process left in the folder is stopped, the backend log is
  copied to `frontend/test-results/backend.log`, and the folder is deleted.

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
| `desktop` | `desktop.spec.js` | 1440×900 | Every page loads without errors, the Feed shows posts, Review keeps, trashes and undoes from the keyboard |
| `layout` | `layout.spec.js` | 1024×768, 1280×800, 1440×900, 1920×1080 | 22 views and 7 dialogs: nothing overlaps, no text is clipped without a title, nothing is off screen, no sideways scroll, click targets of at least 24×24; every page title sits in the same place |
| `layout-zoom` | `layout.spec.js` | 1152×720 at 1.25 device pixels (1440×900 at 125% zoom) | The same layout rules (the title test is skipped) |
| `states` | `states.spec.js` | 1280×800, 1440×900 | Focus rings, hover, popovers, scrolled pages, jumps landing below the header |
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
