import json
import os
import time

from conftest import H
from fakes import owner, write_post

import db
import scanner
import userdata

ALICE = owner("alice.example", 111, "Alice Example")


def data_dir(env):
    return str(env["tmp"] / "data")


def two_kept(env):
    write_post(env["media"], "P1", 1717243200, ALICE, "image")
    write_post(env["media"], "P2", 1717243300, ALICE, "image")
    scanner.scan(env["roots"])
    conn = db.connect()
    db.set_decision(conn, ["instagram:P1", "instagram:P2"], "keep", 7)
    return conn


def wipe(conn):
    conn.execute("DELETE FROM decisions")
    conn.commit()


def kept(conn):
    return [tuple(r) for r in conn.execute("SELECT post_id, decision, at FROM decisions ORDER BY post_id")]


def test_round_trip(env):
    conn = two_kept(env)
    assert userdata.export(conn, "decisions", data_dir(env)) == 2
    path = userdata.path(data_dir(env), "decisions")
    assert path.endswith(os.path.join("data", "userdata", "decisions.json"))
    with open(path) as f:
        doc = json.load(f)
    assert doc == {"version": 1, "rows": [
        {"post_id": "instagram:P1", "decision": "keep", "at": 7},
        {"post_id": "instagram:P2", "decision": "keep", "at": 7},
    ]}
    assert not os.path.exists(path + ".tmp")
    wipe(conn)
    assert userdata.load(conn, "decisions", data_dir(env)) == 2
    assert kept(conn) == [("instagram:P1", "keep", 7), ("instagram:P2", "keep", 7)]


def test_import_skipped_when_table_has_rows(env):
    conn = two_kept(env)
    userdata.export(conn, "decisions", data_dir(env))
    db.set_decision(conn, ["instagram:P2"], None, 8)
    assert userdata.load(conn, "decisions", data_dir(env)) == 0      # never merges into live data
    assert kept(conn) == [("instagram:P1", "keep", 7)]


def test_old_decisions_json_is_read(env):
    conn = two_kept(env)
    wipe(conn)
    with open(os.path.join(data_dir(env), "decisions.json"), "w") as f:
        json.dump({"version": 1, "decisions": [
            {"post_id": "instagram:P1", "decision": "keep", "at": 3},
            {"decision": "keep", "at": 3},                            # no key: skipped
        ]}, f)
    userdata.restore_all(conn, data_dir(env))
    assert kept(conn) == [("instagram:P1", "keep", 3)]
    with open(userdata.path(data_dir(env), "decisions")) as f:              # new file written at once
        assert [r["post_id"] for r in json.load(f)["rows"]] == ["instagram:P1"]
    assert os.path.exists(os.path.join(data_dir(env), "decisions.json"))   # old one left alone
    # the new file wins once it exists
    wipe(conn)
    db.set_decision(conn, ["instagram:P2"], "keep", 9)
    userdata.export(conn, "decisions", data_dir(env))
    wipe(conn)
    userdata.restore_all(conn, data_dir(env))
    assert kept(conn) == [("instagram:P2", "keep", 9)]


def test_first_start_after_upgrade_writes_the_file(env):
    conn = two_kept(env)
    path = userdata.path(data_dir(env), "decisions")
    assert not os.path.exists(path)
    userdata.restore_all(conn, data_dir(env))
    with open(path) as f:
        assert len(json.load(f)["rows"]) == 2


def test_corrupt_file_is_logged_and_ignored(env, capsys):
    conn = two_kept(env)
    wipe(conn)
    path = userdata.path(data_dir(env), "decisions")
    os.makedirs(os.path.dirname(path))
    for bad in ('{"version": 1, "rows": [', '{"rows": "nope"}', "[1, 2]"):
        with open(path, "w") as f:
            f.write(bad)
        userdata.restore_all(conn, data_dir(env))                    # does not raise
        assert kept(conn) == []
        assert "[userdata] Skipped decisions" in capsys.readouterr().out
        assert not os.path.exists(path)                              # moved aside, not overwritten
        aside = [f for f in os.listdir(os.path.dirname(path)) if f.startswith("decisions.json.corrupt-")]
        assert len(aside) == 1
        with open(os.path.join(os.path.dirname(path), aside[0])) as f:
            assert f.read() == bad
        os.remove(os.path.join(os.path.dirname(path), aside[0]))


def test_review_writes_file_after_a_pause(env, client, monkeypatch):
    monkeypatch.setattr(userdata, "DELAY", 0.05)
    write_post(env["media"], "P1", 1717243200, ALICE, "image")
    scanner.scan(env["roots"])
    client.post("/api/review", json={"posts": ["instagram:P1"], "decision": "keep"}, headers=H)
    path = userdata.path(data_dir(env), "decisions")
    for _ in range(100):
        if os.path.exists(path):
            break
        time.sleep(0.02)
    with open(path) as f:
        assert [r["post_id"] for r in json.load(f)["rows"]] == ["instagram:P1"]
    # deleting the post drops its decision, and the file follows
    client.post("/api/delete", json={"posts": ["instagram:P1"]}, headers=H)
    for _ in range(100):
        with open(path) as f:
            if json.load(f)["rows"] == []:
                break
        time.sleep(0.02)
    with open(path) as f:
        assert json.load(f)["rows"] == []


def test_flush_writes_pending_changes_now(env, monkeypatch):
    monkeypatch.setattr(userdata, "DELAY", 60)
    two_kept(env)
    userdata.changed("decisions")
    userdata.flush()
    with open(userdata.path(data_dir(env), "decisions")) as f:
        assert len(json.load(f)["rows"]) == 2
    assert userdata._timers == {}
