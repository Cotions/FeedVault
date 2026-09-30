import os
import sys

import pytest

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture
def env(tmp_path, monkeypatch):
    """An isolated config, database and media root."""
    monkeypatch.setenv("FEEDVAULT_CONFIG", str(tmp_path / "config.json"))
    import config
    import db

    media = tmp_path / "media"
    media.mkdir()
    cfg = config.load()
    cfg["data_directory"] = str(tmp_path / "data")
    cfg["media_roots"] = [str(media)]
    config.save(cfg)
    db.init(config.db_path(cfg))
    return {"tmp": tmp_path, "media": media, "roots": [str(media)]}


@pytest.fixture
def client(env):
    import app as app_module
    app_module.app.testing = True
    c = app_module.app.test_client()
    c.environ_base["HTTP_HOST"] = "localhost:3380"
    return c


H = {"X-FeedVault": "1"}
