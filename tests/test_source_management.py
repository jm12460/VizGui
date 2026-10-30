"""Source validation and credential redaction use temporary test data only."""
import json
import pytest
from vizgui.source_management import public_sources, validate_new_source


def test_local_source_requires_existing_absolute_server_folder(tmp_path):
    folder = tmp_path / "media"
    folder.mkdir()
    assert validate_new_source({"path": str(folder)}) == {"name": "media", "path": str(folder)}
    for path in ("relative", str(tmp_path / "missing"), str(folder / "..") + "\0"):
        with pytest.raises(ValueError):
            validate_new_source({"path": path})


def test_smb_source_is_validated_without_mounting_or_echoing_password():
    source = validate_new_source({"kind": "smb", "smb": {
        "server": "test-nas", "share": "media", "user": "test-user",
        "password": "test-secret", "guest": False}})
    assert source["smb"]["password"] == "test-secret"
    visible = json.dumps(public_sources([source]))
    assert "test-secret" not in visible
    assert "test-user" not in visible
    assert "media" in visible


@pytest.mark.parametrize("source", [
    {"kind": "smb", "smb": {"server": "", "share": "media"}},
    {"kind": "smb", "smb": {"server": "test", "share": "../other"}},
    {"kind": "smb", "smb": {"server": "test", "share": "media", "guest": "false"}},
    {"kind": "unknown"},
])
def test_invalid_sources_are_rejected(source):
    with pytest.raises(ValueError):
        validate_new_source(source)


def test_projection_does_not_leak_unknown_or_nested_secret_fields():
    visible = public_sources([{"name": "Share", "kind": "smb", "unknown": "test-secret",
                               "smb": {"server": "test", "share": "data",
                                       "password": "test-secret", "domain": "test-secret"}}])
    assert "test-secret" not in json.dumps(visible)


def test_guest_share_does_not_store_unused_credentials():
    source = validate_new_source({"kind": "smb", "smb": {"server": "test", "share": "media",
                                 "guest": True, "user": "fake-user", "domain": "fake-domain",
                                 "password": "fake-password"}})
    assert source["smb"]["guest"] is True
    assert source["smb"]["user"] == source["smb"]["domain"] == source["smb"]["password"] == ""
