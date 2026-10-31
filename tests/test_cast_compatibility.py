"""Per-target compatibility records use temporary files and fake devices."""
import json
import os

from vizgui import cast_compatibility as compat


def test_memory_is_scoped_to_device_and_file_and_contains_no_private_names(tmp_path, monkeypatch):
    db = tmp_path / 'compatibility.json'
    monkeypatch.setattr(compat, 'COMPATIBILITY_PATH', str(db))
    one = tmp_path / 'Private Movie.mkv'
    two = tmp_path / 'Other Movie.mkv'
    one.write_bytes(b'one')
    two.write_bytes(b'two')
    a = {'type': 'cast', 'addr': '192.0.2.10', 'cast_port': '8009'}
    b = {'type': 'cast', 'addr': '192.0.2.11', 'cast_port': '8009'}
    key = compat.media_key(a, str(one))
    assert compat.remember(key)
    assert compat.remembered(key)
    assert not compat.remembered(compat.media_key(b, str(one)))
    assert not compat.remembered(compat.media_key(a, str(two)))
    data = db.read_text()
    assert 'Private Movie' not in data and '192.0.2.10' not in data
    assert os.stat(db).st_mode & 0o777 == 0o600
    one.write_bytes(b'changed content')
    assert not compat.remembered(compat.media_key(a, str(one)))


def test_uuid_is_stable_across_address_changes(tmp_path):
    media = tmp_path / 'video.mkv'
    media.write_bytes(b'test')
    target = {'type': 'cast', 'addr': '192.0.2.10',
              'uuid': '12345678-1234-5678-1234-567812345678'}
    key = compat.media_key(target, str(media))
    assert compat.media_key({**target, 'addr': '192.0.2.11'}, str(media)) == key
    assert compat.media_key({**target, 'uuid': '87654321-1234-5678-1234-567812345678'}, str(media)) != key


def test_corrupt_memory_is_never_overwritten(tmp_path, monkeypatch):
    db = tmp_path / 'compatibility.json'
    monkeypatch.setattr(compat, 'COMPATIBILITY_PATH', str(db))
    db.write_text('not valid JSON')
    assert not compat.remember('a' * 64)
    assert db.read_text() == 'not valid JSON'


def test_memory_is_bounded(tmp_path, monkeypatch):
    db = tmp_path / 'compatibility.json'
    monkeypatch.setattr(compat, 'COMPATIBILITY_PATH', str(db))
    monkeypatch.setattr(compat, 'MAX_ENTRIES', 2)
    for key in ('a' * 64, 'b' * 64, 'c' * 64):
        assert compat.remember(key)
    assert json.loads(db.read_text())['entries'] == ['b' * 64, 'c' * 64]
