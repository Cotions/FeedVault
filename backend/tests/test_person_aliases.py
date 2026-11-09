"""A person's aliases: the other names they go by (people.py; docs/API.md "People")."""
import os
import sqlite3

from conftest import H

import db
import people
from test_people import create, get, post
from test_person_attributes import _rebuild, update


def test_a_database_of_version_32_migrates(tmp_path, monkeypatch):
    path = str(tmp_path / "feedvault.db")
    monkeypatch.setattr(db, "MIGRATIONS", db.MIGRATIONS[:32])
    db.migrate(path)
    conn = sqlite3.connect(path)
    conn.execute("INSERT INTO people(name, created_at) VALUES ('Old', 5)")
    conn.commit()
    conn.close()
    monkeypatch.undo()
    db.migrate(path)
    assert os.path.exists(path + ".bak-v32")
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("INSERT INTO person_aliases(person_id, alias, at) VALUES (1, 'Oldie', 1)")
    try:
        conn.execute("INSERT INTO person_aliases(person_id, alias, at) VALUES (1, 'OLDIE', 2)")
        raise AssertionError("an alias twice, in another case")
    except sqlite3.IntegrityError:
        pass
    conn.execute("DELETE FROM people")
    assert conn.execute("SELECT COUNT(*) FROM person_aliases").fetchone()[0] == 0    # ON DELETE CASCADE
    conn.close()


def test_set_read_and_clear(env, client):
    pid = create(client, "Alice")["person"]["id"]
    assert get(client, f"/api/people/{pid}")["aliases"] == []
    p = update(client, pid, {"aliases": ["  Ali   B ", "lissa", "LISSA", "alice"]})["person"]
    assert p["aliases"] == ["Ali B", "lissa"]                  # once each, their own name dropped
    for read in (get(client, f"/api/people/{pid}"), get(client, "/api/people")[0]):
        assert read["aliases"] == ["Ali B", "lissa"]
    assert update(client, pid, {"notes": "x"})["person"]["aliases"] == ["Ali B", "lissa"]    # not sent: kept
    assert update(client, pid, {"aliases": []})["person"]["aliases"] == []


def test_renamed_to_an_alias_drops_it(env, client):
    pid = create(client, "Alice")["person"]["id"]
    update(client, pid, {"aliases": ["Lissa", "Ali"]})
    assert update(client, pid, {"name": "lissa"})["person"]["aliases"] == ["Ali"]


def test_bad_aliases_are_refused(env, client):
    pid = create(client, "A")["person"]["id"]
    for bad in ("x", [7], ['a "b"'], [""], ["n"] * 0 + [f"a{i}" for i in range(people.MAX_ALIASES + 1)]):
        assert "aliases" in update(client, pid, {"aliases": bad}, 400)["error"]


def test_merge_keeps_every_alias_and_the_others_names(env, client):
    a = create(client, "A")["person"]["id"]
    b = create(client, "B")["person"]["id"]
    update(client, a, {"aliases": ["x"]})
    update(client, b, {"aliases": ["X", "y", "A"]})
    m = post(client, "/api/people/merge", {"ids": [a, b]})["person"]
    assert m["aliases"] == ["B", "x", "y"]


def test_merge_under_a_new_name_drops_it_from_the_aliases(env, client):
    a = create(client, "A")["person"]["id"]
    b = create(client, "B")["person"]["id"]
    m = post(client, "/api/people/merge", {"ids": [b, a], "name": "A"})["person"]
    assert (m["name"], m["aliases"]) == ("A", [])


def test_deleting_a_person_drops_their_aliases(env, client):
    pid = create(client, "A")["person"]["id"]
    update(client, pid, {"aliases": ["x"]})
    assert client.delete(f"/api/people/{pid}", headers=H).status_code == 200
    assert db.connect().execute("SELECT COUNT(*) FROM person_aliases").fetchone()[0] == 0


def test_a_rebuild_keeps_them_under_a_new_name(env, client):
    pid = create(client, "A")["person"]["id"]
    update(client, pid, {"aliases": ["x", "y"]})
    update(client, pid, {"name": "Renamed"})
    _rebuild(env)
    p = get(client, f"/api/people/{pid}")
    assert (p["name"], p["aliases"]) == ("Renamed", ["x", "y"])
