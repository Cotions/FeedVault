"""A person's rating and the rating colors (people.py, config.py; docs/API.md
"People" and "Rating colors")."""
import json
import os
import sqlite3

import pytest

import config
import db
import userdata
from test_people import create, get, post
from test_person_attributes import _rebuild, update


def test_a_database_of_version_28_migrates_everyone_unrated(tmp_path, monkeypatch):
    path = str(tmp_path / "feedvault.db")
    monkeypatch.setattr(db, "MIGRATIONS", db.MIGRATIONS[:28])
    db.migrate(path)
    conn = sqlite3.connect(path)
    conn.execute("INSERT INTO people(name, notes, created_at, gender) VALUES ('Old', 'kept', 5, 'Female')")
    conn.execute("INSERT INTO people(name, created_at) VALUES ('Other', 6)")
    conn.commit()
    conn.close()
    monkeypatch.undo()
    db.migrate(path)
    assert os.path.exists(path + ".bak-v28")
    conn = sqlite3.connect(path)
    assert conn.execute("SELECT name, notes, gender, rating FROM people ORDER BY id").fetchall() == [
        ("Old", "kept", "Female", None), ("Other", "", "", None)]
    conn.execute("UPDATE people SET rating = 10 WHERE name = 'Old'")
    for bad in (0, 11, -1):
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE people SET rating = ? WHERE name = 'Other'", (bad,))
    conn.close()


def test_new_people_are_unrated(env, client):
    p = create(client, "A")["person"]
    assert p["rating"] is None
    assert get(client, "/api/people")[0]["rating"] is None


def test_set_read_and_clear(env, client):
    pid = create(client, "A")["person"]["id"]
    assert update(client, pid, {"rating": 7})["person"]["rating"] == 7
    for read in (get(client, f"/api/people/{pid}"), get(client, "/api/people")[0]):
        assert read["rating"] == 7
    p = update(client, pid, {"gender": "Female"})["person"]
    assert p["rating"] == 7                                           # not sent: it stays
    for edge in (1, 10):
        assert update(client, pid, {"rating": edge})["person"]["rating"] == edge
    assert update(client, pid, {"rating": None})["person"]["rating"] is None
    assert get(client, f"/api/people/{pid}")["rating"] is None


@pytest.mark.parametrize("bad", [0, 11, -1, 3.5, 7.0, "x", "7", True, [7], {"v": 7}])
def test_anything_but_1_to_10_or_null_is_refused(env, client, bad):
    pid = create(client, "A")["person"]["id"]
    update(client, pid, {"rating": 4})
    assert "rating must be a whole number from 1 to 10" in update(client, pid, {"rating": bad}, 400)["error"]
    assert get(client, f"/api/people/{pid}")["rating"] == 4           # nothing changed


def test_a_rating_alone_is_a_change(env, client):
    pid = create(client, "A")["person"]["id"]
    assert "rating" in update(client, pid, {}, 400)["error"]
    assert update(client, pid, {"rating": None})["ok"]                # clearing an unrated one is fine


def test_export_and_import_keep_ratings(env, client):
    a = create(client, "A")["person"]["id"]
    b = create(client, "B")["person"]["id"]
    create(client, "C")
    update(client, a, {"rating": 7})
    update(client, b, {"rating": 10})
    userdata.flush()
    with open(userdata.path(config.load()["data_directory"], "people")) as f:
        rows = {r["name"]: r["rating"] for r in json.load(f)["rows"]}
    assert rows == {"A": 7, "B": 10, "C": None}
    _rebuild(env)
    assert {p["name"]: p["rating"] for p in get(client, "/api/people")} == {"A": 7, "B": 10, "C": None}


def test_a_hand_edited_or_older_file_keeps_the_person_not_a_bad_rating(env, client):
    base = config.load()["data_directory"]
    os.makedirs(os.path.join(base, "userdata"), exist_ok=True)
    rows = [{"name": "A", "created_at": 1, "id": 4, "rating": 11},
            {"name": "B", "created_at": 1, "id": 5, "rating": 3.5},
            {"name": "C", "created_at": 1, "id": 6, "rating": "7"},
            {"name": "D", "created_at": 1, "id": 7, "rating": 6},
            {"name": "E", "created_at": 1, "id": 8}]                     # a file from before ratings
    with open(userdata.path(base, "people"), "w") as f:
        json.dump({"version": 1, "rows": rows}, f)
    conn = db.connect()
    with conn:
        conn.execute("DELETE FROM people")
    userdata.restore_all(conn, base)
    got = {p["name"]: p["rating"] for p in get(client, "/api/people")}
    assert got == {"A": None, "B": None, "C": None, "D": 6, "E": None}


@pytest.mark.parametrize("ratings, kept", [
    ((3, 8, None), 8),             # the highest, wherever it is
    ((9, 2, None), 9),             # the target's when it is the highest
    ((None, 5, None), 5),          # the target unrated: another's
    ((None, None, None), None),    # nobody rated: unrated
])
def test_merge_keeps_the_highest_rating(env, client, ratings, kept):
    ids = [create(client, n)["person"]["id"] for n in "ABC"]
    for pid, r in zip(ids, ratings):
        if r is not None:
            update(client, pid, {"rating": r})
    m = post(client, "/api/people/merge", {"ids": ids})["person"]
    assert m["id"] == ids[0] and m["rating"] == kept
    assert get(client, f"/api/people/{ids[0]}")["rating"] == kept


# ---------------------------------------------------------------------------
# Rating colors (config.json, Settings → Appearance)
# ---------------------------------------------------------------------------

def colors(client, value, ok=True):
    r = post(client, "/api/config", {"rating_colors": value})
    assert r["ok"] is ok, r
    return r


def test_unset_is_the_defaults(env, client):
    got = get(client, "/api/config")["rating_colors"]
    assert got == config.RATING_COLORS_DEFAULT
    assert [b["min"] for b in got["bands"]] == [1, 4, 6, 8, 10]


def test_set_read_back_and_reset(env, client):
    value = {"enabled": False, "bands": [{"min": 3, "color": "#AABBCC"}, {"min": 9, "color": "#000000"}]}
    r = colors(client, value)
    want = {"enabled": False, "bands": [{"min": 3, "color": "#aabbcc"}, {"min": 9, "color": "#000000"}]}
    assert r["config"]["rating_colors"] == want
    assert get(client, "/api/config")["rating_colors"] == want
    with open(config.config_path()) as f:
        assert json.load(f)["rating_colors"] == want
    # One band is enough, ten the most.
    colors(client, {"enabled": True, "bands": [{"min": 10, "color": "#123456"}]})
    colors(client, {"enabled": True, "bands": [{"min": i, "color": "#123456"} for i in range(1, 11)]})
    # null: back to the defaults, and out of the file.
    assert colors(client, None)["config"]["rating_colors"] == config.RATING_COLORS_DEFAULT
    with open(config.config_path()) as f:
        assert "rating_colors" not in json.load(f)


@pytest.mark.parametrize("value, why", [
    ({"enabled": True, "bands": [{"min": 1, "color": "red"}]}, "#rrggbb"),
    ({"enabled": True, "bands": [{"min": 1, "color": "#fff"}]}, "#rrggbb"),
    ({"enabled": True, "bands": [{"min": 1, "color": "#12345g"}]}, "#rrggbb"),
    ({"enabled": True, "bands": [{"min": 1, "color": "#1234567"}]}, "#rrggbb"),
    ({"enabled": True, "bands": [{"min": 1, "color": None}]}, "#rrggbb"),
    ({"enabled": True, "bands": [{"min": 4, "color": "#111111"}, {"min": 4, "color": "#222222"}]}, "go up"),
    ({"enabled": True, "bands": [{"min": 6, "color": "#111111"}, {"min": 4, "color": "#222222"}]}, "go up"),
    ({"enabled": True, "bands": [{"min": 0, "color": "#111111"}]}, "1 to 10"),
    ({"enabled": True, "bands": [{"min": 11, "color": "#111111"}]}, "1 to 10"),
    ({"enabled": True, "bands": [{"min": 2.5, "color": "#111111"}]}, "1 to 10"),
    ({"enabled": True, "bands": [{"min": True, "color": "#111111"}]}, "1 to 10"),
    ({"enabled": True, "bands": [{"min": "4", "color": "#111111"}]}, "1 to 10"),
    ({"enabled": True, "bands": []}, "1 to 10 bands"),
    ({"enabled": True, "bands": [{"min": i, "color": "#111111"} for i in range(1, 12)]}, "1 to 10 bands"),
    ({"enabled": True, "bands": [{"min": 1}]}, "{ min, color }"),
    ({"enabled": True, "bands": [{"min": 1, "color": "#111111", "x": 1}]}, "{ min, color }"),
    ({"enabled": "yes", "bands": [{"min": 1, "color": "#111111"}]}, "true or false"),
    ({"bands": [{"min": 1, "color": "#111111"}]}, "rating_colors must be"),
    (["#111111"], "rating_colors must be"),
])
def test_bad_colors_are_refused_and_nothing_saved(env, client, value, why):
    before = get(client, "/api/config")["rating_colors"]
    assert why in colors(client, value, ok=False)["error"]
    assert get(client, "/api/config")["rating_colors"] == before


def test_a_hand_edited_config_falls_back_to_the_defaults():
    assert config.rating_colors({"rating_colors": {"enabled": True, "bands": [{"min": 5, "color": "blue"}]}}) \
        == config.RATING_COLORS_DEFAULT
    assert config.rating_colors({"rating_colors": "x"}) == config.RATING_COLORS_DEFAULT
    # A copy: changing what it returns never changes the defaults.
    config.rating_colors({})["bands"][0]["color"] = "#000000"
    assert config.RATING_COLORS_DEFAULT["bands"][0]["color"] == "#d9534f"


def test_band_lookup():
    bands = [{"min": 4, "color": "#444444"}, {"min": 6, "color": "#666666"}]
    assert config.rating_band(5, bands)["min"] == 4           # between two bands: the lower one
    assert config.rating_band(6, bands)["min"] == 6
    assert config.rating_band(10, bands)["min"] == 6
    assert config.rating_band(3, bands) is None               # below the first band: the plain border
    assert config.rating_band(None, bands) is None            # unrated: always the plain border
    defaults = config.RATING_COLORS_DEFAULT["bands"]
    assert [config.rating_band(r, defaults)["color"] for r in (1, 3, 4, 7, 9, 10)] == [
        "#d9534f", "#d9534f", "#f0ad4e", "#e6c84a", "#5cb85c", "#d4af37"]
