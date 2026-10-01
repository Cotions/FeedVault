import json
import os
import signal
import sys
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
    wait_for(lambda: state(job_id) not in ("queued", "running"), timeout)
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
    monkeypatch.setattr(jobs, "_history", jobs.collections.OrderedDict())
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
out.write(b"y" * 10_000_000)
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


def test_progress_lines_keep_their_last_state(runner):
    runner["kind"]("progress", "import sys; sys.stdout.write('10%\\r50%\\r100%\\r\\ndone\\n')")
    job = ended(jobs.submit("progress", {})["id"])
    assert [ln["text"] for ln in jobs.log(job["id"])["lines"]] == ["100%", "done"]


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
