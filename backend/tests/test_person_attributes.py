"""A person's gender, country and labels (people.py; docs/API.md "People")."""
import json
import os
import sqlite3

from conftest import H

import config
import db
import people
import userdata
from test_people import create, get, post


def update(client, pid, body, status=200):
    return post(client, f"/api/people/{pid}", body, status)


def test_a_database_of_version_23_migrates(tmp_path, monkeypatch):
    path = str(tmp_path / "feedvault.db")
    monkeypatch.setattr(db, "MIGRATIONS", db.MIGRATIONS[:23])
    db.migrate(path)
    conn = sqlite3.connect(path)
    conn.execute("INSERT INTO people(name, notes, created_at) VALUES ('Old', 'kept', 5)")
    conn.commit()
    conn.close()
    monkeypatch.undo()
    db.migrate(path)
    assert os.path.exists(path + ".bak-v23")
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys=ON")
    assert conn.execute("SELECT name, notes, gender, country FROM people").fetchall() == [("Old", "kept", "", "")]
    conn.execute("INSERT INTO person_labels(person_id, label, at) VALUES (1, 'Travel', 1)")
    try:
        conn.execute("INSERT INTO person_labels(person_id, label, at) VALUES (1, 'travel', 2)")
        raise AssertionError("a label twice, in another case")
    except sqlite3.IntegrityError:
        pass
    conn.execute("DELETE FROM people")
    assert conn.execute("SELECT COUNT(*) FROM person_labels").fetchone()[0] == 0    # ON DELETE CASCADE
    conn.close()


def test_new_people_have_none(env, client):
    p = create(client, "A")["person"]
    assert (p["gender"], p["country"], p["labels"]) == ("", "", [])
    assert get(client, "/api/people")[0]["labels"] == []


def test_set_read_and_clear(env, client):
    pid = create(client, "A")["person"]["id"]
    p = update(client, pid, {"gender": "  non-BINARY ", "country": " fr ", "labels": ["Travel", "cosplay", "travel"]})["person"]
    assert (p["gender"], p["country"], p["labels"]) == ("Non-binary", "FR", ["cosplay", "Travel"])
    p = update(client, pid, {"gender": "Two   spirit"})["person"]
    assert p["gender"] == "Two spirit" and p["country"] == "FR"        # what is not sent stays
    for read in (get(client, f"/api/people/{pid}"), get(client, "/api/people")[0]):
        assert (read["gender"], read["country"], read["labels"]) == ("Two spirit", "FR", ["cosplay", "Travel"])
    p = update(client, pid, {"gender": "", "country": "", "labels": []})["person"]
    assert (p["gender"], p["country"], p["labels"]) == ("", "", [])


def test_labels_replace_and_keep_their_date(env, client, monkeypatch):
    pid = create(client, "A")["person"]["id"]
    monkeypatch.setattr("time.time", lambda: 100)
    update(client, pid, {"labels": ["one", "two"]})
    monkeypatch.setattr("time.time", lambda: 200)
    p = update(client, pid, {"labels": ["ONE", "three"]})["person"]
    assert p["labels"] == ["ONE", "three"]                              # retyped in another case: takes it
    rows = db.connect().execute("SELECT label, at FROM person_labels ORDER BY label").fetchall()
    assert [tuple(r) for r in rows] == [("ONE", 100), ("three", 200)]
    # Case is ASCII only, as for tags (SQLite NOCASE): "Été" and "été" are two labels.
    p = update(client, pid, {"labels": ["Été", "été", "one"]})["person"]
    assert sorted(p["labels"]) == ["one", "Été", "été"]


def test_bad_values_are_refused(env, client):
    pid = create(client, "A")["person"]["id"]
    for body in ({"country": "ZZ"}, {"country": "FRA"}, {"country": 33}, {"country": "F"}):
        assert "ISO 3166-1" in update(client, pid, body, 400)["error"]
    update(client, pid, {"gender": "x" * 41}, 400)
    update(client, pid, {"gender": 3}, 400)
    update(client, pid, {"gender": "a\x07b"}, 400)
    assert update(client, pid, {"gender": "a\nb"})["person"]["gender"] == "a b"     # spaces collapsed
    update(client, pid, {"gender": "x" * 40})
    update(client, pid, {"labels": "travel"}, 400)
    update(client, pid, {"labels": ['say "hi"']}, 400)
    update(client, pid, {"labels": [""]}, 400)
    update(client, pid, {"labels": [5]}, 400)
    update(client, pid, {"labels": ["x" * 65]}, 400)
    update(client, pid, {"labels": [f"l{i}" for i in range(51)]}, 400)
    update(client, pid, {"labels": [f"l{i}" for i in range(50)]})
    p = get(client, f"/api/people/{pid}")
    assert p["country"] == "" and len(p["labels"]) == 50                 # nothing half done
    update(client, 999, {"country": "FR"}, 404)


def test_attributes_are_what_people_have(env, client):
    a = create(client, "A")["person"]["id"]
    b = create(client, "B")["person"]["id"]
    c = create(client, "C")["person"]["id"]
    update(client, a, {"gender": "Female", "labels": ["Travel", "cosplay"]})
    update(client, b, {"gender": "female", "labels": ["travel"]})
    update(client, c, {"gender": "Agender", "labels": ["Travel", "food"]})
    r = get(client, "/api/people/attributes")
    assert r["labels"] == [{"name": "Travel", "count": 3}, {"name": "cosplay", "count": 1}, {"name": "food", "count": 1}]
    assert r["genders"] == [{"name": "Female", "count": 2}, {"name": "Agender", "count": 1}]
    assert r["presets"] == ["Female", "Male", "Non-binary"]
    assert len(r["countries"]) == 249 and {"FR", "US", "JP"} <= set(r["countries"])
    assert all(people.clean_country(c) == c for c in r["countries"])
    # Not post tags: the Tags page never sees them.
    assert get(client, "/api/tags") == []


def test_merge_keeps_every_label_and_the_first_gender_and_country(env, client):
    a = create(client, "A")["person"]["id"]
    b = create(client, "B")["person"]["id"]
    c = create(client, "C")["person"]["id"]
    update(client, a, {"labels": ["travel"]})
    update(client, b, {"gender": "Male", "country": "JP", "labels": ["Travel", "food"]})
    update(client, c, {"gender": "Female", "country": "FR", "labels": ["cosplay"]})
    m = post(client, "/api/people/merge", {"ids": [a, b, c]})["person"]
    assert m["id"] == a
    assert (m["gender"], m["country"], m["labels"]) == ("Male", "JP", ["cosplay", "food", "travel"])
    assert db.connect().execute("SELECT COUNT(*) FROM person_labels").fetchone()[0] == 3
    assert get(client, "/api/people/attributes")["labels"][0] == {"name": "cosplay", "count": 1}


def test_deleting_a_person_drops_their_labels(env, client):
    pid = create(client, "A")["person"]["id"]
    update(client, pid, {"labels": ["travel"]})
    assert client.delete(f"/api/people/{pid}", headers=H).status_code == 200
    assert db.connect().execute("SELECT COUNT(*) FROM person_labels").fetchone()[0] == 0
    assert get(client, "/api/people/attributes")["labels"] == []


def _rebuild(env):
    """feedvault.db deleted, then started again from the userdata files."""
    userdata.flush()
    path = config.db_path(config.load())
    db.init(str(env["tmp"] / "other.db"))                     # let go of the file
    for suffix in ("", "-wal", "-shm"):
        if os.path.exists(path + suffix):
            os.remove(path + suffix)
    db.init(path)
    userdata.restore_all(db.connect(), config.load()["data_directory"])


def test_a_rebuild_keeps_them_under_a_new_name(env, client):
    pid = create(client, "A")["person"]["id"]
    update(client, pid, {"gender": "Two spirit", "country": "CA", "labels": ["travel", "food"]})
    update(client, pid, {"name": "Renamed"})                   # the labels' file names them by name
    _rebuild(env)
    p = get(client, f"/api/people/{pid}")
    assert (p["name"], p["gender"], p["country"], p["labels"]) == ("Renamed", "Two spirit", "CA", ["food", "travel"])


def test_a_hand_edited_file_keeps_the_person_not_a_bad_value(env, client):
    base = config.load()["data_directory"]
    os.makedirs(os.path.join(base, "userdata"), exist_ok=True)
    rows = {"people": [{"name": "A", "created_at": 1, "id": 4, "gender": "x" * 99, "country": "ZZ"},
                       {"name": "B", "created_at": 1, "id": 5, "gender": "male", "country": "de"},
                       {"name": "C", "created_at": 1, "id": 6}],
            "person_labels": [{"person": "A", "label": "  two   words ", "at": 1}, {"person": "A", "label": 'bad "one"'},
                              {"person": "Nobody", "label": "lost"}, {"person": "B", "label": 7}]}
    for name, r in rows.items():
        with open(userdata.path(base, name), "w") as f:
            json.dump({"version": 1, "rows": r}, f)
    conn = db.connect()
    with conn:
        conn.execute("DELETE FROM people")
    userdata.restore_all(conn, base)
    got = {p["name"]: (p["gender"], p["country"], p["labels"]) for p in get(client, "/api/people")}
    assert got == {"A": ("", "", ["two words"]), "B": ("Male", "DE", []), "C": ("", "", [])}
