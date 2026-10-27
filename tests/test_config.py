import os
import tempfile
from unittest import mock

from vizgui import config


def test_migrate_default_seeds_media_dirs_and_vlc():
    with mock.patch.object(config.platforms, "default_media_dirs",
                           return_value=[("Videos", "/home/u/Videos"),
                                         ("Music", "/home/u/Music")]):
        cfg = config._migrate_default()
    assert cfg["sources"] == [{"name": "Videos", "path": "/home/u/Videos"},
                              {"name": "Music", "path": "/home/u/Music"}]
    assert any(t["type"] == "vlc" for t in cfg["targets"])
    assert not any(t.get("type") == "cast" for t in cfg["targets"])
    assert cfg["web"]["enabled"] is True
    assert cfg["auto_next"] is True


def test_migrate_default_empty_when_no_media_dirs():
    with mock.patch.object(config.platforms, "default_media_dirs", return_value=[]):
        cfg = config._migrate_default()
    assert cfg["sources"] == []
    assert any(t["type"] == "vlc" for t in cfg["targets"])


def test_ensure_web_secrets_adds_and_is_idempotent():
    cfg = {}
    assert config.ensure_web_secrets(cfg) is True
    assert cfg["web"]["token"] and cfg["web"]["pin"]
    assert len(cfg["web"]["pin"]) == 6
    assert config.ensure_web_secrets(cfg) is False   # already present


def test_active_cast():
    cfg = {"targets": [{"type": "vlc"}, {"type": "cast", "name": "TV"}]}
    assert config.active_cast(cfg)["name"] == "TV"
    assert config.active_cast({"targets": []}) is None


def test_set_active_cast_updates_module_state():
    config.set_active_cast({"addr": "1.2.3.4", "cast_port": "8010",
                            "smartcast_host": "1.2.3.4:7345", "auth_token": "tok"})
    assert config.TV_IP == "1.2.3.4"
    assert config.CAST_PORT == "8010"
    assert config.HOST == "1.2.3.4:7345"
    assert config.TOK == "tok"


def test_set_active_cast_empty_defaults_port():
    config.set_active_cast({})
    assert config.TV_IP is None and config.CAST_PORT == "8009"


def test_save_load_roundtrip():
    with tempfile.TemporaryDirectory() as d:
        cfgpath = os.path.join(d, "config.json")
        with mock.patch.object(config, "CONFIG_DIR", d), \
             mock.patch.object(config, "CONFIG_PATH", cfgpath):
            config.save_config({"sources": [{"name": "A", "path": "/x"}],
                                "targets": [], "web": {"port": 9}, "auto_next": False})
            loaded = config.load_config()
            # chmod 600 applied
            assert oct(os.stat(cfgpath).st_mode)[-3:] == "600"
    assert loaded["sources"][0]["name"] == "A"
    assert loaded["web"]["port"] == 9
    assert loaded["auto_next"] is False
    assert loaded["web"]["token"]   # load_config backfills secrets


def test_load_config_missing_file_migrates():
    with tempfile.TemporaryDirectory() as d:
        cfgpath = os.path.join(d, "nope.json")
        with mock.patch.object(config, "CONFIG_DIR", d), \
             mock.patch.object(config, "CONFIG_PATH", cfgpath):
            loaded = config.load_config()
            assert config.FRESH_INSTALL is True   # genuine first run
    assert any(t["type"] == "vlc" for t in loaded["targets"])
    assert loaded["web"]["token"]


def test_load_config_existing_file_not_fresh():
    with tempfile.TemporaryDirectory() as d:
        cfgpath = os.path.join(d, "config.json")
        with mock.patch.object(config, "CONFIG_DIR", d), \
             mock.patch.object(config, "CONFIG_PATH", cfgpath):
            config.save_config({"sources": [], "targets": [], "web": {"port": 8950}})
            config.load_config()
            assert config.FRESH_INSTALL is False   # existing config -> no wizard
