import json
import os
import signal
import subprocess
import sys
import threading
import time

import pytest

from fakes import owner, write_post

import jobs

ALICE = owner("alice.example", 111, "Alice Example")

# Waits on a gate file, so a test decides when the job ends.
WAIT = "import os, sys, time\nwhile not os.path.exists(sys.argv[1]): time.sleep(0.02)\nprint('released')"


def wait_for(pred, timeout=10):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        v = pred()
        if v:
            return v
        time.sleep(0.02)
    raise AssertionError("timed out")


def state(job_id):
    return jobs.get(job_id)["state"]


def ended(job_id, timeout=10):
    wait_for(lambda: job_id not in jobs._active and state(job_id) not in ("queued", "running"), timeout)
    return jobs.get(job_id)


def alive(pid):
    """Running, not a zombie waiting for its parent."""
    try:
        with open(f"/proc/{pid}/stat") as f:
            return f.read().rsplit(")", 1)[1].split()[0] != "Z"
    except FileNotFoundError:
        return False


@pytest.fixture
def runner(env, monkeypatch):
    """The job runner with test kinds that run Python scripts. Every job is
    stopped afterwards."""
    monkeypatch.setattr(jobs, "_active", jobs.collections.OrderedDict())
    monkeypatch.setattr(jobs, "_closing", False)
    monkeypatch.setattr(jobs, "KILL_AFTER", 0.5)
    added = []

    def kind(name, script, group="test", params=None, args=lambda p: [], **extra):
        jobs.register(name, label=name, params=params or {}, group=group,
                      build=lambda p: {"tool": sys.executable, "args": ["-c", script, *args(p)],
                                       **{k: (v(p) if callable(v) else v) for k, v in extra.items()}})
        added.append(name)

    def gate_kind(name, group):
        kind(name, WAIT, group=group, params={"gate": {"type": "text"}}, args=lambda p: [p["gate"]])

    yield {"kind": kind, "gate_kind": gate_kind, "tmp": env["tmp"], "media": env["media"]}
    jobs.shutdown()
    # A runner thread still finishing would land in the next test's queue.
    for t in threading.enumerate():
        if t.name.startswith("job-") and not t.name.endswith("-log"):
            t.join(10)
    for name in added:
        jobs._kinds.pop(name, None)


def test_runs_and_logs(runner):
    runner["kind"]("hello", "print('one'); print('two')")
    job = jobs.submit("hello", {})
    assert job["state"] in ("queued", "running") and job["argv"][1:3] == ["-c", "print('one'); print('two')"]
    job = ended(job["id"])
    assert (job["state"], job["exit_code"], job["message"]) == ("done", 0, "finished")
    log = jobs.log(job["id"])
    assert [ln["text"] for ln in log["lines"]] == ["one", "two"]
    assert [ln["n"] for ln in log["lines"]] == [1, 2]
    assert jobs.log(job["id"], after=1)["lines"] == [{"n": 2, "text": "two"}]


def test_injection_arrives_as_one_literal_argument(runner):
    runner["kind"]("echo", "import json, sys; print(json.dumps(sys.argv[1:]))",
                   params={"target": {"type": "text"}}, args=lambda p: [p["target"]])
    for target in ["x; rm -rf ~", "$(id)", "`id` && echo pwned | sh", "a\nb"]:
        job = ended(jobs.submit("echo", {"target": target})["id"])
        assert job["state"] == "done"
        assert json.loads(jobs.log(job["id"])["lines"][0]["text"]) == [target]


def test_unknown_kind_and_bad_params(runner):
    with pytest.raises(jobs.BadRequest):
        jobs.submit("rm", {})
    with pytest.raises(jobs.BadRequest):
        jobs.submit(["tool-version"], {})
    for params in [{}, {"tool": "bash"}, {"tool": "yt-dlp; id"}, {"tool": ["yt-dlp"]},
                   {"tool": "yt-dlp", "extra": "1"}, "yt-dlp", [1]]:
        with pytest.raises(jobs.BadRequest):
            jobs.submit("tool-version", params)
    runner["kind"]("text", "pass", params={"t": {"type": "text", "max": 5}})
    for params in [{"t": ""}, {"t": "toolong"}, {"t": "a\0b"}, {"t": 3}]:
        with pytest.raises(jobs.BadRequest):
            jobs.submit("text", params)
    assert jobs.listing()["jobs"] == []


def test_queue_order_within_a_group(runner):
    runner["gate_kind"]("gated", "g")
    gates = [runner["tmp"] / f"gate{i}" for i in range(3)]
    ids = [jobs.submit("gated", {"gate": str(g)})["id"] for g in gates]
    wait_for(lambda: state(ids[0]) == "running")
    time.sleep(0.2)
    assert [state(i) for i in ids] == ["running", "queued", "queued"]
    # Released out of order: the queue still starts them oldest first.
    gates[2].touch()
    gates[0].touch()
    ended(ids[0])
    wait_for(lambda: state(ids[1]) == "running")
    assert state(ids[2]) == "queued"
    gates[1].touch()
    assert [ended(i)["state"] for i in ids] == ["done", "done", "done"]


def test_groups_run_in_parallel_up_to_the_limit(runner):
    for g in "abc":
        runner["gate_kind"](f"gated-{g}", g)
    gate = runner["tmp"] / "gate"
    ids = [jobs.submit(f"gated-{g}", {"gate": str(gate)})["id"] for g in "abc"]
    wait_for(lambda: state(ids[0]) == "running" and state(ids[1]) == "running")
    time.sleep(0.2)
    assert state(ids[2]) == "queued"                   # MAX_RUNNING = 2
    assert jobs.listing()["running"] == 2 and jobs.listing()["queued"] == 1
    gate.touch()
    assert [ended(i)["state"] for i in ids] == ["done", "done", "done"]


def test_crash_exit_code(runner):
    runner["kind"]("crash", "import sys; print('working'); print('boom: disk full'); sys.exit(3)")
    job = ended(jobs.submit("crash", {})["id"])
    assert (job["state"], job["exit_code"], job["message"]) == ("failed", 3, "boom: disk full")


def test_missing_tool(runner, monkeypatch):
    monkeypatch.setenv("PATH", str(runner["tmp"]))
    job = ended(jobs.submit("tool-version", {"tool": "gallery-dl"})["id"])
    assert job["state"] == "failed" and job["exit_code"] is None
    assert job["message"] == "gallery-dl not found; set its path in Settings"


def test_configured_tool_path(runner, monkeypatch):
    import config
    tool = runner["tmp"] / "bin" / "yt-dlp"
    tool.parent.mkdir()
    tool.write_text(f"#!{sys.executable}\nprint('2099.01.01')\n")
    tool.chmod(0o755)
    monkeypatch.setenv("PATH", "")
    cfg = config.load()
    cfg["tools"] = {"yt-dlp": str(tool)}
    config.save(cfg)
    job = ended(jobs.submit("tool-version", {"tool": "yt-dlp"})["id"])
    assert (job["state"], job["result"], job["message"]) == ("done", {"version": "2099.01.01"}, "2099.01.01")
    tool.chmod(0o644)                                  # no longer executable: not found, no fallback
    job = ended(jobs.submit("tool-version", {"tool": "yt-dlp"})["id"])
    assert job["state"] == "failed" and "not found" in job["message"]


def test_tool_version_argv(runner, monkeypatch):
    monkeypatch.setenv("PATH", "")
    argv = {t: ended(jobs.submit("tool-version", {"tool": t})["id"])["argv"] for t in jobs.TOOLS}
    assert argv == {"instaloader": ["instaloader", "--version"], "gallery-dl": ["gallery-dl", "--version"],
                    "yt-dlp": ["yt-dlp", "--version"], "ffmpeg": ["ffmpeg", "-version"]}


def test_clean_tools():
    import config
    ok, err = config.clean_tools({"ffmpeg": sys.executable}, jobs.TOOLS)
    assert ok is None and "must be named ffmpeg" in err
    assert config.clean_tools({"bash": "/bin/bash"}, jobs.TOOLS)[0] is None
    assert config.clean_tools({"ffmpeg": "/no/such/ffmpeg"}, jobs.TOOLS)[0] is None
    assert config.clean_tools({"ffmpeg": "relative/ffmpeg"}, jobs.TOOLS)[0] is None
    assert config.clean_tools({"ffmpeg": ""}, jobs.TOOLS) == ({}, None)
    assert config.clean_tools(["ffmpeg"], jobs.TOOLS)[0] is None


def test_cancel_kills_the_whole_process_group(runner):
    pids = runner["tmp"] / "pids"
    runner["kind"]("tree", f"""
import os, subprocess, sys, time
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(1000)"])
open({str(pids)!r}, "w").write(f"{{os.getpid()}} {{child.pid}}")
time.sleep(1000)
""")
    job = jobs.submit("tree", {})
    wait_for(lambda: pids.exists() and len(pids.read_text().split()) == 2)
    parent, grandchild = map(int, pids.read_text().split())
    assert alive(parent) and alive(grandchild)
    assert jobs.cancel(job["id"])["state"] == "running"
    job = ended(job["id"])
    assert job["state"] == "cancelled" and job["exit_code"] == -signal.SIGTERM
    wait_for(lambda: not alive(parent) and not alive(grandchild))


def test_hanging_job_is_killed(runner):
    ready = runner["tmp"] / "ready"
    runner["kind"]("stubborn", f"""
import signal, time
signal.signal(signal.SIGTERM, signal.SIG_IGN)
open({str(ready)!r}, "w").close()
while True: time.sleep(1)
""")
    job = jobs.submit("stubborn", {})
    wait_for(ready.exists)
    jobs.cancel(job["id"])
    time.sleep(0.2)
    assert state(job["id"]) == "running"                # ignored SIGTERM
    job = ended(job["id"], timeout=5)                  # SIGKILL after KILL_AFTER
    assert job["state"] == "cancelled" and job["exit_code"] == -signal.SIGKILL


def test_cancel_queued_and_unknown(runner):
    runner["gate_kind"]("gated", "g")
    gate = runner["tmp"] / "gate"
    first = jobs.submit("gated", {"gate": str(gate)})["id"]
    second = jobs.submit("gated", {"gate": str(gate)})["id"]
    assert jobs.cancel(second)["state"] == "cancelled"
    assert state(second) == "cancelled" and jobs.log(second)["lines"] == []
    assert jobs.cancel(second) is None and jobs.cancel(99999) is None
    gate.touch()
    assert ended(first)["state"] == "done"


def rss_kb():
    with open("/proc/self/status") as f:
        return next(int(line.split()[1]) for line in f if line.startswith("VmRSS:"))


def test_huge_output_stays_in_the_ring_buffer(runner):
    # 100 MB: 100,000 lines of 1 KB, then one 10 MB line with no newline.
    runner["kind"]("flood", """
import sys
out = sys.stdout.buffer
line = b"x" * 1023 + b"\\n"
for i in range(100_000):
    out.write(line)
out.write(b"y " * 5_000_000)
""")
    before = rss_kb()
    job = jobs.submit("flood", {})
    peak = before
    while state(job["id"]) in ("queued", "running"):
        peak = max(peak, rss_kb())
        with jobs._lock:
            live = jobs._active.get(job["id"])
            assert live is None or len(live.lines) <= jobs.LOG_LINES
        time.sleep(0.01)
    assert ended(job["id"])["state"] == "done"
    assert peak - before < 60_000                       # KB; 5000 lines of 1 KB is ~5 MB
    tail = jobs.log(job["id"])
    assert len(tail["lines"]) == jobs.TAIL_KEPT and tail["first"] == 100_001 - jobs.TAIL_KEPT + 1
    last = tail["lines"][-1]
    assert last["n"] == 100_001 and last["text"].startswith("y") and len(last["text"]) <= jobs.LINE_MAX + 2


def test_log_pages(runner):
    runner["kind"]("many", "for i in range(2500): print(i)")
    job_id = jobs.submit("many", {})["id"]
    # Read live, the way the dashboard does.
    seen, after = [], 0
    while True:
        page = jobs.log(job_id, after)
        seen += [ln["text"] for ln in page["lines"]]
        after = page["next"]
        if page["state"] not in ("queued", "running") and not page["more"]:
            break
        time.sleep(0.01)
    # Finished: only the tail is kept, so a slow reader has a gap.
    assert seen[-1] == "2499" and len(seen) >= jobs.TAIL_KEPT


def test_progress_redraws_are_throttled(runner):
    runner["kind"]("progress", "import sys; sys.stdout.write('10%\\r50%\\r100%\\r\\ndone\\n')")
    job = ended(jobs.submit("progress", {})["id"])
    # The first redraw shows, the next ones within a second do not; the
    # line a \\r\\n ends is always kept.
    assert [ln["text"] for ln in jobs.log(job["id"])["lines"]] == ["10%", "100%", "done"]


def test_progress_without_newline_shows_live(runner, monkeypatch):
    monkeypatch.setattr(jobs, "PROGRESS_EVERY", 0)
    gate = runner["tmp"] / "gate"
    runner["kind"]("bar", f"""
import os, sys, time
for i in range(3):
    sys.stdout.write(f"[download] {{i * 50}}%\\r"); sys.stdout.flush()
while not os.path.exists({str(gate)!r}): time.sleep(0.02)
""")
    job_id = jobs.submit("bar", {})["id"]
    # No newline yet, the job still running: the redraws are already there.
    wait_for(lambda: len(jobs.log(job_id)["lines"]) == 3)
    assert state(job_id) == "running"
    assert [ln["text"] for ln in jobs.log(job_id)["lines"]] == ["[download] 0%", "[download] 50%", "[download] 100%"]
    gate.touch()
    ended(job_id)


def test_invalid_utf8_is_replaced(runner):
    runner["kind"]("bytes", "import sys; sys.stdout.buffer.write(b'caf\\xe9\\n')")
    job = ended(jobs.submit("bytes", {})["id"])
    assert jobs.log(job["id"])["lines"][0]["text"] == "caf�"


def test_rescan_target_reports_new_posts(runner):
    folder = runner["media"] / "alice.example"
    folder.mkdir()
    script = f"""
import sys
sys.path.insert(0, {os.path.dirname(os.path.abspath(__file__))!r})
from fakes import owner, write_post
a = owner("alice.example", 111, "Alice Example")
write_post({str(folder)!r}, "P1", 1717243200, a, "image")
write_post({str(folder / "sub")!r}, "P2", 1717243300, a, "image")
"""
    runner["kind"]("download", script, rescan=str(folder))
    job = ended(jobs.submit("download", {})["id"])
    assert (job["state"], job["result"], job["message"]) == ("done", {"added": 2, "updated": 0}, "2 new posts")
    assert "[feedvault] indexing" in jobs.log(job["id"])["lines"][-1]["text"]
    job = ended(jobs.submit("download", {})["id"])        # the same files again
    assert job["message"] == "0 new posts"


def test_failed_job_is_not_indexed(runner):
    folder = runner["media"] / "bob"
    write_post(folder, "P9", 1717243200, ALICE, "image")
    runner["kind"]("bad-download", "import sys; sys.exit(1)", rescan=str(folder))
    job = ended(jobs.submit("bad-download", {})["id"])
    assert job["state"] == "failed" and job["result"] is None
    import db
    assert db.connect().execute("SELECT COUNT(*) FROM posts").fetchone()[0] == 0


def test_rescan_target_must_be_inside_a_media_root(runner):
    runner["kind"]("outside", "pass", rescan=str(runner["tmp"]))
    with pytest.raises(jobs.BadRequest):
        jobs.submit("outside", {})


def test_shutdown_stops_running_jobs(runner):
    pids = runner["tmp"] / "pid"
    runner["kind"]("long", f"import os, time; open({str(pids)!r}, 'w').write(str(os.getpid())); time.sleep(1000)",
                   group="g")
    runner["gate_kind"]("gated", "g")
    job = jobs.submit("long", {})["id"]
    queued = jobs.submit("gated", {"gate": str(runner["tmp"] / "never")})["id"]
    wait_for(pids.exists)
    jobs.shutdown()
    assert not alive(int(pids.read_text()))
    assert state(job) == "interrupted" and state(queued) == "interrupted"
    with pytest.raises(jobs.BadRequest):
        jobs.submit("long", {})


def test_history_is_kept_in_sqlite(runner, monkeypatch):
    runner["kind"]("hello", "print('hi'); import sys; sys.exit(2)")
    job = ended(jobs.submit("hello", {})["id"])
    # A new process: nothing in memory, the row and its tail remain.
    monkeypatch.setattr(jobs, "_active", jobs.collections.OrderedDict())
    again = jobs.get(job["id"])
    assert again == job and again["params"] == {} and again["argv"][0] == sys.executable
    assert (again["state"], again["exit_code"], again["message"]) == ("failed", 2, "hi")
    assert jobs.log(job["id"])["lines"] == [{"n": 1, "text": "hi"}]
    assert jobs.get(job["id"] + 1) is None and jobs.log(job["id"] + 1) is None


def test_interrupted_on_restart(runner):
    import db
    runner["gate_kind"]("gated", "g")
    gate = str(runner["tmp"] / "never")
    running = jobs.submit("gated", {"gate": gate})["id"]
    queued = jobs.submit("gated", {"gate": gate})["id"]
    wait_for(lambda: state(running) == "running")
    # The backend dies without shutdown(): the rows still say running and queued.
    states = dict(db.connect().execute("SELECT id, state FROM jobs").fetchall())
    assert states == {running: "running", queued: "queued"}
    jobs.recover()                                     # the next start
    with jobs._lock:
        orphans = dict(jobs._active)
        jobs._active.clear()
    for job_id in (running, queued):
        job = jobs.get(job_id)
        assert job["state"] == "interrupted" and job["message"] == jobs.INTERRUPTED
        assert job["ended_at"] is None                 # unknown
    with jobs._lock:
        jobs._active.update(orphans)                   # so teardown stops the "old" process



def test_history_keeps_the_last_100(runner, monkeypatch):
    monkeypatch.setattr(jobs, "HISTORY_KEPT", 5)
    runner["kind"]("quick", "pass")
    ids = [ended(jobs.submit("quick", {})["id"])["id"] for _ in range(7)]
    listed = jobs.listing()
    assert [j["id"] for j in listed["jobs"]] == ids[::-1][:5]
    assert (listed["running"], listed["queued"]) == (0, 0)
    assert jobs.get(ids[0]) is None


def test_jobs_are_not_user_data():
    import userdata
    assert "jobs" not in userdata.REGISTRY


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

from conftest import H  # noqa: E402


def test_api_start_watch_and_list(runner, client, monkeypatch):
    monkeypatch.setenv("PATH", os.path.dirname(sys.executable))
    kinds = client.get("/api/jobs/kinds", headers=H).get_json()
    tv = next(k for k in kinds if k["kind"] == "tool-version")
    assert tv["params"]["tool"]["choices"] == ["instaloader", "gallery-dl", "yt-dlp", "ffmpeg"]
    r = client.post("/api/jobs", json={"kind": "tool-version", "params": {"tool": "gallery-dl"}}, headers=H)
    assert r.status_code == 200 and r.get_json()["ok"] is True
    job_id = r.get_json()["job"]["id"]
    job = ended(job_id)
    assert client.get(f"/api/jobs/{job_id}", headers=H).get_json() == job
    assert job["message"] == "gallery-dl not found; set its path in Settings"
    listed = client.get("/api/jobs", headers=H).get_json()
    assert listed["running"] == 0 and [j["id"] for j in listed["jobs"]] == [job_id]
    runner["kind"]("hello", "print('a'); print('b')")
    job_id = client.post("/api/jobs", json={"kind": "hello"}, headers=H).get_json()["job"]["id"]
    ended(job_id)
    log = client.get(f"/api/jobs/{job_id}/log?after=1", headers=H).get_json()
    assert log["lines"] == [{"n": 2, "text": "b"}] and log["next"] == 2 and log["state"] == "done"
    assert client.get(f"/api/jobs/{job_id}/log?after=x", headers=H).get_json()["next"] == 2   # bad after: 0
    assert client.post(f"/api/jobs/{job_id}/cancel", headers=H).status_code == 409


def test_api_rejects_bad_requests(runner, client):
    for body in [{"kind": "nope"}, {"kind": "tool-version"}, {"kind": "tool-version", "params": {"tool": "sh"}},
                 {"kind": "tool-version", "params": {"tool": "yt-dlp", "argv": ["sh"]}},
                 {"argv": ["sh", "-c", "id"]}, {"command": "id"}, [1], "x"]:
        r = client.post("/api/jobs", json=body, headers=H)
        assert r.status_code == 400 and r.get_json()["ok"] is False, body
    assert client.get("/api/jobs/999", headers=H).status_code == 404
    assert client.get("/api/jobs/999/log", headers=H).status_code == 404
    assert client.post("/api/jobs/999/cancel", headers=H).status_code == 404
    assert client.get("/api/jobs", headers=H).get_json()["jobs"] == []


def test_api_guard(runner, client):
    body = {"kind": "tool-version", "params": {"tool": "yt-dlp"}}
    assert client.post("/api/jobs", json=body).status_code == 403
    assert client.post("/api/jobs", json=body, headers={**H, "Host": "evil.example"}).status_code == 403
    for path in ["/api/jobs", "/api/jobs/kinds", "/api/jobs/1", "/api/jobs/1/log"]:
        assert client.get(path).status_code == 403
    assert client.post("/api/jobs/1/cancel").status_code == 403
    assert jobs.listing()["jobs"] == []


def test_api_cancel(runner, client):
    runner["gate_kind"]("gated", "g")
    gate = str(runner["tmp"] / "never")
    first = client.post("/api/jobs", json={"kind": "gated", "params": {"gate": gate}}, headers=H).get_json()["job"]
    wait_for(lambda: state(first["id"]) == "running")
    r = client.post(f"/api/jobs/{first['id']}/cancel", headers=H).get_json()
    assert r["ok"] is True
    assert ended(first["id"])["state"] == "cancelled"


def test_api_tool_paths(env, client):
    tool = env["tmp"] / "venv" / "instaloader"
    tool.parent.mkdir()
    tool.write_text("#!/bin/sh\n")
    tool.chmod(0o755)
    r = client.post("/api/config", json={"tools": {"instaloader": str(tool)}}, headers=H).get_json()
    assert r["ok"] is True and r["config"]["tools"] == {"instaloader": str(tool)}
    for tools in [{"instaloader": "/bin/sh"}, {"sh": "/bin/sh"}, {"yt-dlp": str(tool)}, ["x"],
                  {"instaloader": str(env["tmp"] / "nope" / "instaloader")}]:
        r = client.post("/api/config", json={"tools": tools}, headers=H).get_json()
        assert r["ok"] is False, tools
    # A refused tool path saves nothing else either.
    r = client.post("/api/config", json={"tools": {"ffmpeg": "/bin/sh"}, "media_roots": []}, headers=H).get_json()
    assert r["ok"] is False
    cfg = client.get("/api/config", headers=H).get_json()
    assert cfg["tools"] == {"instaloader": str(tool)} and cfg["media_roots"] == env["roots"]
    r = client.post("/api/config", json={"tools": {"instaloader": ""}}, headers=H).get_json()
    assert r["ok"] is True and r["config"]["tools"] == {}


def test_cancel_while_indexing_is_refused(runner, monkeypatch, client):
    folder = runner["media"] / "f"
    folder.mkdir()
    runner["kind"]("dl", "pass", rescan=str(folder))
    indexing, release = jobs.threading.Event(), jobs.threading.Event()
    real = jobs.scanner.index_dirs

    def slow(roots, dirs, new=False, since=None):
        indexing.set()
        release.wait(10)
        return real(roots, dirs, new, since)
    monkeypatch.setattr(jobs.scanner, "index_dirs", slow)
    job_id = jobs.submit("dl", {})["id"]
    assert indexing.wait(10)
    with pytest.raises(jobs.TooLate):
        jobs.cancel(job_id)
    r = client.post(f"/api/jobs/{job_id}/cancel", headers=H)
    assert r.status_code == 409 and "indexing" in r.get_json()["error"]
    release.set()
    assert ended(job_id)["state"] == "done"


def test_full_scan_goes_through_the_scanner(runner, monkeypatch):
    import scanner
    kicks = []
    monkeypatch.setattr(jobs.scanner.hashing, "kick", lambda: kicks.append(1))
    folder = runner["media"] / "alice.example"
    write_post(folder, "P1", 1717243200, ALICE, "image")
    runner["kind"]("full", "pass", rescan=str(folder), full_scan=True)
    job = ended(jobs.submit("full", {})["id"])
    assert job["result"] == {"added": 1, "updated": 0}
    assert scanner.status()["last"]["added"] == 1 and not scanner.status()["running"] and kicks


def test_rescan_skips_what_a_scan_skips(runner):
    folder = runner["media"] / "dl"
    write_post(folder, "P1", 1717243200, ALICE, "image")
    write_post(folder / "venv" , "P2", 1717243300, ALICE, "image")
    write_post(folder / "tool", "P3", 1717243400, ALICE, "image")
    (folder / "tool" / "pyvenv.cfg").write_text("")
    write_post(folder / ".hidden", "P4", 1717243500, ALICE, "image")
    runner["kind"]("dl", "pass", rescan=str(folder))
    assert ended(jobs.submit("dl", {})["id"])["result"] == {"added": 1, "updated": 0}


# ---------------------------------------------------------------------------
# Leftover processes after a crash
# ---------------------------------------------------------------------------

# A FeedVault that starts a long job, then dies without stopping it.
CRASHER = """
import os, signal, sys, time
sys.path[:0] = [{backend!r}, {tests!r}]
import config, db, jobs
db.init(config.db_path(config.load()))
jobs.register("long", label="long", params={{}}, group="g", build=lambda p: {{"tool": sys.executable, "args": [
    "-c", "import subprocess, sys, time\\n"
          "c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(1000)'])\\n"
          "open({pids!r}, 'w').write(f'{{c.pid}}')\\n"
          "time.sleep(1000)"]}})
job = jobs.submit("long", {{}})
while not os.path.exists({pids!r}):
    time.sleep(0.02)
os.kill(os.getpid(), signal.SIGKILL)
"""


def test_crash_leaves_a_process_that_the_next_start_stops(runner):
    import db
    pids = runner["tmp"] / "pids"
    backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    crasher = subprocess.run([sys.executable, "-c", CRASHER.format(
        backend=backend, tests=os.path.join(backend, "tests"), pids=str(pids))], timeout=30)
    assert crasher.returncode == -signal.SIGKILL
    [row] = db.connect().execute("SELECT id, state, pid, pid_start, pid_exe FROM jobs").fetchall()
    leader, child = row["pid"], int(pids.read_text())
    try:
        assert row["state"] == "running" and alive(leader) and alive(child)
        assert jobs.identity(leader) == (row["pid_start"], row["pid_exe"])
        assert os.path.realpath(row["pid_exe"]) == os.path.realpath(sys.executable)
        jobs.recover()                                 # the next start
        wait_for(lambda: not alive(leader) and not alive(child))
        assert jobs.get(row["id"])["state"] == "interrupted"
    finally:
        for pid in (leader, child):
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


def bystander():
    """A process in a group of its own that FeedVault did not start."""
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(1000)"], start_new_session=True)
    wait_for(lambda: jobs.identity(proc.pid))
    return proc


@pytest.mark.parametrize("change", ["start", "exe", "gone", "none"])
def test_only_the_exact_process_is_stopped(runner, change):
    import db
    proc = bystander()
    try:
        start, exe = jobs.identity(proc.pid)
        pid = proc.pid
        if change == "start":
            start += 1                                 # the pid was reused by another process
        elif change == "exe":
            exe = "/usr/bin/instaloader"
        elif change == "gone":
            pid = 2 ** 22 + 1                          # above pid_max: no such process
        elif change == "none":
            start = exe = None                         # recorded without an identity
        conn = db.connect()
        with conn:
            conn.execute("INSERT INTO jobs(kind, params, argv, cwd, lock_group, state, created_at, started_at, "
                         "pid, pid_start, pid_exe) VALUES ('x', '{}', '[\"x\"]', '/', 'g', 'running', 1, 2, ?, ?, ?)",
                         (pid, start, exe))
        jobs.recover()
        time.sleep(0.3)
        assert proc.poll() is None and alive(proc.pid)
        assert conn.execute("SELECT state FROM jobs").fetchone()[0] == "interrupted"
    finally:
        proc.kill()
        proc.wait()


def test_exact_match_is_stopped_even_when_it_ignores_sigterm(runner):
    import db
    proc = subprocess.Popen([sys.executable, "-c", "import signal, time\n"
                             "signal.signal(signal.SIGTERM, signal.SIG_IGN)\nprint('ready', flush=True)\n"
                             "time.sleep(1000)"], start_new_session=True, stdout=subprocess.PIPE)
    try:
        proc.stdout.readline()
        start, exe = jobs.identity(proc.pid)
        conn = db.connect()
        with conn:
            conn.execute("INSERT INTO jobs(kind, params, argv, cwd, lock_group, state, created_at, started_at, "
                         "pid, pid_start, pid_exe) VALUES ('x', '{}', '[\"x\"]', '/', 'g', 'running', 1, 2, ?, ?, ?)",
                         (proc.pid, start, exe))
        t0 = time.monotonic()
        jobs.recover()
        assert proc.wait(5) == -signal.SIGKILL and time.monotonic() - t0 >= jobs.KILL_AFTER
    finally:
        proc.kill()
        proc.wait()


def test_running_job_records_its_process(runner):
    import db
    runner["gate_kind"]("gated", "g")
    gate = runner["tmp"] / "gate"
    job = jobs.submit("gated", {"gate": str(gate)})
    wait_for(lambda: state(job["id"]) == "running")
    row = wait_for(lambda: db.connect().execute("SELECT pid, pid_start, pid_exe FROM jobs WHERE id = ? "
                                                "AND pid IS NOT NULL", (job["id"],)).fetchone())
    assert jobs.identity(row["pid"]) == (row["pid_start"], row["pid_exe"])
    gate.touch()
    assert ended(job["id"])["state"] == "done"


def sleeper():
    """A harmless process in a group of its own, as a job's tool would be."""
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(1000)"], start_new_session=True)
    wait_for(lambda: jobs.identity(proc.pid))
    return proc


def left_running(proc, start=None):
    """A job row as a killed FeedVault leaves it, for ``proc``."""
    import db
    found_start, exe = jobs.identity(proc.pid)
    conn = db.connect()
    with conn:
        return conn.execute("INSERT INTO jobs(kind, params, argv, cwd, lock_group, state, created_at, started_at, "
                            "pid, pid_start, pid_exe) VALUES ('x', '{}', '[\"sleep\"]', '/', 'g', 'running', 1, 2, "
                            "?, ?, ?)", (proc.pid, found_start if start is None else start, exe)).lastrowid


def test_leftover_sleep_is_stopped_and_logged(runner, capsys):
    proc = sleeper()
    try:
        job_id = left_running(proc)
        jobs.recover()
        assert proc.wait(5) == -signal.SIGTERM
        out = capsys.readouterr().out
        assert f"[jobs] stopping process {proc.pid} (job #{job_id}), left running when FeedVault last stopped" in out
        assert jobs.get(job_id)["state"] == "interrupted"
        assert [ln["text"] for ln in jobs.log(job_id)["lines"]] == [
            f"[feedvault] process {proc.pid} was still running after FeedVault stopped: stopped at the next start"]
    finally:
        proc.kill()
        proc.wait()


def test_leftover_sleep_with_another_start_time_is_left_alone(runner, capsys):
    proc = sleeper()
    try:
        job_id = left_running(proc, start=jobs.identity(proc.pid)[0] - 1)    # the pid was reused since
        jobs.recover()
        time.sleep(0.3)
        assert proc.poll() is None and alive(proc.pid)
        assert f"process {proc.pid} of job #{job_id} is another program now: left alone" in capsys.readouterr().out
        assert jobs.get(job_id)["state"] == "interrupted" and jobs.log(job_id)["lines"] == []
    finally:
        proc.kill()
        proc.wait()


def test_without_proc_nothing_is_stopped(runner, monkeypatch, capsys):
    proc = sleeper()
    try:
        job_id = left_running(proc)
        monkeypatch.setattr(jobs, "PROC", str(runner["tmp"] / "no-proc"))
        jobs.recover()
        time.sleep(0.3)
        assert proc.poll() is None and alive(proc.pid)
        assert "processes left running when FeedVault last stopped are not looked for" in capsys.readouterr().out
        assert jobs.get(job_id)["state"] == "interrupted"
    finally:
        proc.kill()
        proc.wait()


def test_a_process_that_exits_between_check_and_signal_is_not_signalled(runner, monkeypatch):
    """The pidfd pins the process checked: once it has exited, its pid is
    not signalled, whoever has it by then."""
    proc = sleeper()
    recorded = jobs.identity(proc.pid)
    fd = jobs._pin(proc.pid, recorded)
    assert fd is not None
    proc.kill()
    proc.wait()                                    # reaped: the pid is free for anyone
    sent = []
    monkeypatch.setattr(jobs.os, "killpg", lambda pid, sig: sent.append(pid))
    try:
        assert jobs._signal_group(fd, proc.pid, recorded, signal.SIGTERM) is False and sent == []
    finally:
        jobs._close(fd)


def test_leftover_is_stopped_without_pidfds(runner, monkeypatch):
    """No pidfds (an old kernel, a seccomp filter): the identity is checked
    again right before each signal instead."""
    import errno

    def no_pidfd(pid):
        raise OSError(errno.ENOSYS, "Function not implemented")
    monkeypatch.setattr(jobs.os, "pidfd_open", no_pidfd)
    proc = sleeper()
    try:
        left_running(proc)
        jobs.recover()
        assert proc.wait(5) == -signal.SIGTERM
    finally:
        proc.kill()
        proc.wait()


SECRETS = ("FAKESESSION0001", "FAKECOOKIE0002", "FAKEBEARER0003token", "session-carol")
LEAKY = "\n".join([
    "print('starting')",
    "print('GET https://example.com/api sessionid=FAKESESSION0001; csrftoken=abc')",
    "print('')",
    "print('Cookie: ds_user_id=1; FAKECOOKIE0002=yes')",
    "print('Authorization: Bearer FAKEBEARER0003token')",
    "print('Loaded session from /home/someone/.config/instaloader/session-carol.')",
    "print('done', flush=True)",
    "import os, sys, time",
    "while not os.path.exists(sys.argv[-1]): time.sleep(0.02)",
])


def test_output_is_scrubbed_before_it_is_kept(runner, client):
    from conftest import H
    seen = []

    def outcome(params, code, lines, index, note):
        seen.extend(t for _, t in lines)       # the hooks parse it as printed
        return "done", None, "finished"
    gate, script = runner["tmp"] / "gate", runner["tmp"] / "leaky.py"
    script.write_text(LEAKY)                   # in a file: the job's argv is stored and shown too
    runner["kind"]("leaky", "import runpy, sys; runpy.run_path(sys.argv[1])", params={"gate": {"type": "text"}},
                   args=lambda p: [str(script), p["gate"]])
    jobs._kinds["leaky"].outcome = outcome
    job = jobs.submit("leaky", {"gate": str(gate)})
    # Live, while it runs (held by the gate), as the dashboard reads it.
    live = wait_for(lambda: len(jobs.log(job["id"])["lines"]) == 7 and jobs.log(job["id"]))
    assert live["state"] == "running" and not any(s in ln["text"] for s in SECRETS for ln in live["lines"])
    gate.touch()
    ended(job["id"])
    assert any("FAKESESSION0001" in t for t in seen) and len(seen) == 7
    got = jobs.log(job["id"])["lines"]
    # One line for one line, the empty one too, numbered as printed.
    assert [ln["n"] for ln in got] == list(range(1, 8))
    assert [ln["text"] for ln in got] == [
        "starting", "GET https://example.com/api sessionid=…; csrftoken=…", "", "Cookie: …",
        "Authorization: … …", "Loaded session from <private path>", "done"]
    import db
    row = db.connect().execute("SELECT * FROM jobs WHERE id = ?", (job["id"],)).fetchone()
    stored = json.dumps([row[k] for k in row.keys()])
    api = client.get(f"/api/jobs/{job['id']}", headers=H).get_data(as_text=True) + \
        client.get(f"/api/jobs/{job['id']}/log", headers=H).get_data(as_text=True) + \
        client.get("/api/jobs", headers=H).get_data(as_text=True)
    for s in SECRETS:
        assert s not in stored and s not in api
    assert "/.config/instaloader" not in stored + api


def test_a_failed_job_s_message_is_scrubbed(runner):
    runner["kind"]("leaky-fails", "print('token=FAKESESSION0001 at ~/.config/instaloader/x'); raise SystemExit(1)")
    job = ended(jobs.submit("leaky-fails", {})["id"])
    assert job["state"] == "failed" and "FAKESESSION0001" not in job["message"]
    assert "/.config/instaloader" not in job["message"]


# ---------------------------------------------------------------------------
# Hardening (#73)
# ---------------------------------------------------------------------------

def _own_tool(folder, name="yt-dlp"):
    folder.mkdir(mode=0o755, exist_ok=True)
    tool = folder / name
    tool.write_text(f"#!{sys.executable}\nprint('2099.01.01')\n")
    tool.chmod(0o755)
    return tool


@pytest.mark.parametrize("open_up, says", [
    (lambda tool: tool.chmod(0o775), "the file ({tool}) is writable by group or others (chmod go-w '{tool}')"),
    (lambda tool: tool.chmod(0o757), "the file ({tool}) is writable by group or others (chmod go-w '{tool}')"),
    (lambda tool: tool.parent.chmod(0o775),
     "its folder ({folder}) is writable by group or others (chmod go-w '{folder}')"),
    (lambda tool: tool.parent.chmod(0o777),
     "its folder ({folder}) is writable by group or others (chmod go-w '{folder}')"),
])
def test_a_tool_path_others_can_swap_is_refused_when_saved(env, client, open_up, says):
    tool = _own_tool(env["tmp"] / "tools")
    open_up(tool)
    r = client.post("/api/config", json={"tools": {"yt-dlp": str(tool)}}, headers=H).get_json()
    assert r["ok"] is False
    assert r["error"] == "yt-dlp: " + says.format(tool=tool, folder=tool.parent)


def test_a_tool_path_of_another_user_is_refused(env, client, monkeypatch):
    import config
    tool = _own_tool(env["tmp"] / "tools")
    real_stat = os.stat

    def theirs(path, *a, **k):
        st = real_stat(path, *a, **k)
        if os.fspath(path) == str(tool):
            return os.stat_result((st.st_mode, st.st_ino, st.st_dev, st.st_nlink, os.getuid() + 1, *st[5:10]))
        return st

    monkeypatch.setattr(config.os, "stat", theirs)
    r = client.post("/api/config", json={"tools": {"yt-dlp": str(tool)}}, headers=H).get_json()
    assert r["ok"] is False and r["error"] == f"yt-dlp: the file ({tool}) belongs to another user"


def test_a_tool_path_through_a_symlink_is_checked_where_it_leads(env, client):
    tool = _own_tool(env["tmp"] / "real")
    (env["tmp"] / "bin").mkdir(mode=0o755)
    link = env["tmp"] / "bin" / "yt-dlp"
    link.symlink_to(tool)
    assert client.post("/api/config", json={"tools": {"yt-dlp": str(link)}}, headers=H).get_json()["ok"]
    tool.parent.chmod(0o777)
    r = client.post("/api/config", json={"tools": {"yt-dlp": str(link)}}, headers=H).get_json()
    assert r["error"] == f"yt-dlp: its folder ({tool.parent}) is writable by group or others (chmod go-w '{tool.parent}')"


def test_a_tool_path_others_can_swap_by_now_is_not_run(runner, client, monkeypatch):
    import config
    import downloaders
    tool = _own_tool(runner["tmp"] / "tools")
    monkeypatch.setenv("PATH", "")
    assert client.post("/api/config", json={"tools": {"yt-dlp": str(tool)}}, headers=H).get_json()["ok"]
    assert ended(jobs.submit("tool-version", {"tool": "yt-dlp"})["id"])["state"] == "done"
    tool.chmod(0o777)                          # after it was saved
    why = (f"yt-dlp: the path set in Settings is refused: the file ({tool}) is writable by group or others "
           f"(chmod go-w '{tool}')")
    assert jobs.tool_path("yt-dlp") is None and jobs.tool_lookup("yt-dlp") == (None, why)
    job = ended(jobs.submit("tool-version", {"tool": "yt-dlp"})["id"])
    assert (job["state"], job["message"], job["exit_code"]) == ("failed", why, None)
    # Settings → Downloaders says why.
    info = downloaders.detect("yt-dlp", config.load())
    assert (info["found"], info["path_error"]) == (False, why)


@pytest.mark.parametrize("path", ["", ".", "bin", "./bin", ":bin", "bin:"])
def test_a_relative_path_entry_finds_nothing(runner, monkeypatch, path):
    """Popen would look a relative result up again, in the job's folder."""
    _own_tool(runner["tmp"] / "bin")
    _own_tool(runner["tmp"], "yt-dlp")
    monkeypatch.chdir(runner["tmp"])
    monkeypatch.setenv("PATH", path)
    assert jobs.tool_path("yt-dlp") is None
    assert jobs.tool_lookup("yt-dlp") == (None, "yt-dlp not found; set its path in Settings")
    assert jobs.tool_path("./yt-dlp") is None and jobs.tool_path("bin/yt-dlp") is None


def test_absolute_path_entries_beside_relative_ones_still_count(runner, monkeypatch):
    tool = _own_tool(runner["tmp"] / "bin")
    _own_tool(runner["tmp"], "yt-dlp")
    monkeypatch.chdir(runner["tmp"])
    monkeypatch.setenv("PATH", f".::{tool.parent}")
    assert jobs.tool_path("yt-dlp") == str(tool)
    assert jobs.tool_path(str(tool)) == str(tool)


def test_a_tool_path_below_a_folder_others_can_write_to_is_refused(env, client):
    """#73 review: the folders above the tool's folder count too (sticky ones excepted)."""
    top = env["tmp"] / "tools"
    (top / "venv").mkdir(parents=True)
    tool = _own_tool(top / "venv" / "bin")
    top.chmod(0o777)
    try:
        r = client.post("/api/config", json={"tools": {"yt-dlp": str(tool)}}, headers=H).get_json()
        assert r["error"] == f"yt-dlp: a folder above it ({top}) is writable by group or others (chmod go-w '{top}')"
        top.chmod(0o1777)
        assert client.post("/api/config", json={"tools": {"yt-dlp": str(tool)}}, headers=H).get_json()["ok"]
    finally:
        top.chmod(0o755)
