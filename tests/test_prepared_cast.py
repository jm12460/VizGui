"""Seekable Cast preparation using only temporary media and fake devices."""
import json
import os
import shutil
import subprocess
from unittest.mock import Mock

import pytest

from vizgui import history
from vizgui.playback import prepared, targets


def make_media(path):
    ffmpeg = shutil.which('ffmpeg')
    if not ffmpeg or not shutil.which('ffprobe'):
        pytest.skip('ffmpeg/ffprobe not available')
    result = subprocess.run([ffmpeg, '-v', 'error', '-f', 'lavfi', '-i',
                             'color=c=black:s=160x90:r=10', '-f', 'lavfi', '-i',
                             'anullsrc=r=48000:cl=stereo', '-t', '2', '-c:v',
                             'libx264', '-c:a', 'aac', str(path)],
                            capture_output=True, text=True, timeout=20)
    if result.returncode:
        pytest.skip('test encoders unavailable: ' + result.stderr[-200:])


def test_prepared_file_is_seekable_and_owned_directory_is_cleaned(tmp_path):
    source = tmp_path / 'episode.mkv'
    make_media(source)
    cache = tmp_path / 'cache'
    result = prepared.prepare(str(source), str(cache))
    assert result is not None
    owner, output = result
    probe = subprocess.run(['ffprobe', '-v', 'error', '-show_entries',
                            'stream=codec_name', '-of', 'json', output],
                           capture_output=True, text=True, timeout=10, check=True)
    assert {s['codec_name'] for s in json.loads(probe.stdout)['streams']} == {'h264', 'aac'}
    subprocess.run(['ffmpeg', '-v', 'error', '-ss', '1', '-i', output, '-frames:v',
                    '1', '-f', 'null', '-'], capture_output=True, timeout=10, check=True)
    assert source.exists()
    assert history.recover_match(prepared.prepared_name(str(source)),
                                 {'path': str(source), 'title': 'Episode'}, [])['path'] == str(source)
    owner.cleanup()
    assert not os.path.exists(output)
    assert source.exists()


def test_oversized_or_failed_preparation_preserves_source_and_leaves_no_files(tmp_path, monkeypatch):
    source = tmp_path / 'episode.mkv'
    make_media(source)
    cache = tmp_path / 'cache'
    monkeypatch.setattr(prepared, 'MAX_SOURCE_BYTES', 1)
    assert prepared.prepare(str(source), str(cache)) is None
    assert source.exists()
    assert not cache.exists()


def test_cast_uses_prepared_file_and_cleans_it_on_shutdown(tmp_path, monkeypatch):
    source = tmp_path / 'episode.mkv'
    source.write_bytes(b'episode')
    output = tmp_path / 'prepared.mp4'
    output.write_bytes(b'prepared')
    owner = Mock()
    target = targets.CastTarget()
    monkeypatch.setattr(targets.media, 'vcodec', lambda _: 'h264')
    monkeypatch.setattr(targets.prepared, 'prepare', lambda *_: (owner, str(output)))
    monkeypatch.setattr(targets, 'gc', lambda *args, **kw: (True, ''))
    monkeypatch.setattr(targets.time, 'sleep', lambda _: None)
    launch = Mock(return_value=Mock(poll=lambda: None, pid=1, returncode=0))
    monkeypatch.setattr(targets.platforms, 'launch_caster', launch)
    assert target.start(str(source), [str(source)], False, resume=42) == 'playing'
    assert target.live is False
    assert launch.call_args.args[0][-4:] == ['load', '--start-time', '42', str(output)]
    owner.cleanup.assert_not_called()
    target.shutdown()
    owner.cleanup.assert_called_once()
    assert source.read_bytes() == b'episode'


def test_superseded_preparation_does_not_stop_or_replace_cast(tmp_path, monkeypatch):
    source = tmp_path / 'episode.mkv'
    source.write_bytes(b'episode')
    owner = Mock()
    target = targets.CastTarget()
    target.handle = Mock()
    monkeypatch.setattr(targets.media, 'vcodec', lambda _: 'h264')
    monkeypatch.setattr(targets.prepared, 'prepare', lambda *_: (owner, '/tmp/prepared.mp4'))
    cast_command = Mock()
    monkeypatch.setattr(targets, 'gc', cast_command)
    result = target.start_guarded(str(source), [str(source)], False, current=lambda: False)
    assert result == {'error': 'Playback request superseded'}
    owner.cleanup.assert_called_once()
    target.handle.terminate.assert_not_called()
    cast_command.assert_not_called()


def test_cancel_during_handoff_does_not_launch_new_caster(tmp_path, monkeypatch):
    source = tmp_path / 'episode.mkv'
    source.write_bytes(b'episode')
    owner = Mock()
    target = targets.CastTarget()
    monkeypatch.setattr(targets.media, 'vcodec', lambda _: 'h264')
    monkeypatch.setattr(targets.prepared, 'prepare', lambda *_: (owner, '/tmp/prepared.mp4'))
    monkeypatch.setattr(targets, 'gc', lambda *args, **kwargs: (True, ''))
    monkeypatch.setattr(target, '_wait_idle', lambda: None)
    launch = Mock()
    monkeypatch.setattr(targets.platforms, 'launch_caster', launch)
    checks = iter((True, False))
    result = target.start_guarded(str(source), [str(source)], False, current=lambda: next(checks))
    assert result == {'error': 'Playback request superseded'}
    launch.assert_not_called()
    owner.cleanup.assert_called_once()
