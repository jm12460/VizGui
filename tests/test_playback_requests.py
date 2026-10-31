"""Launch acknowledgements and receiver-confirmed state, without device I/O."""
from unittest.mock import Mock

import pytest

from vizgui import engine, history


@pytest.fixture
def launch(tmp_path, monkeypatch):
    path = tmp_path / "episode.mp4"
    path.write_bytes(b"test media")
    monkeypatch.setattr(history, "CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(history, "HISTORY_PATH", str(tmp_path / "history.json"))
    monkeypatch.setattr(history, "CURRENT_PATH", str(tmp_path / "current.json"))
    monkeypatch.setattr(engine.cast_compatibility, "COMPATIBILITY_PATH", str(tmp_path / "compatibility.json"))
    monkeypatch.setattr(engine.config, "CFG", {"auto_next": False,
                                             "targets": [{"type": "cast", "addr": "192.0.2.10"}],
                                             "sources": [{"path": str(tmp_path)}]})
    monkeypatch.setattr(engine.probe, "probe", lambda _: {"chapters": []})
    pending, messages = [], []
    e = engine.Engine(run_async=lambda fn, done=None: pending.append((fn, done)),
                      on_status=messages.append)
    e.active = Mock()
    e.targets = {"cast": e.active}
    e.active.available.return_value = (True, "")
    e.active.start.return_value = "playing"
    e.active.status.return_value = {"idle": True, "busy": True}
    e.target_index = 0
    return e, str(path), pending, messages


def playing(e):
    e._poll_done({"idle": False, "state": "PLAYING", "pos": 60, "dur": 600})


def test_accepted_command_does_not_claim_playback_has_started(launch):
    e, path, pending, _ = launch
    reply = e.web_do("cast", {"path": path, "resume": 60})
    request = e.web_status()["playback_request"]
    assert reply == {"ok": True, "request_id": request["id"], "state": "accepted"}
    assert request["state"] == "starting"
    e.active.start.assert_not_called()
    fn, done = pending.pop(0)
    done(fn())
    assert e.web_status()["playback_request"]["state"] == "starting"
    e._poll_done({"idle": True, "busy": True})
    assert e.web_status()["playback_request"]["state"] == "starting"
    playing(e)
    assert e.web_status()["playback_request"]["state"] == "playing"


@pytest.mark.parametrize("failure", ["device launch failed", {"error": "worker raised an error"}])
def test_launch_error_is_visible_and_not_erased_by_later_polls(launch, failure):
    e, path, pending, messages = launch
    e.cast(path)
    pending.pop(0)[1](failure)
    expected = failure["error"] if isinstance(failure, dict) else failure
    request = e.web_status()["playback_request"]
    assert request["state"] == "failed"
    assert request["error"] == expected
    assert messages[-1] == expected
    e._poll_done({"idle": True, "busy": False})
    playing(e)  # old or external playback must not turn a failed launch into success
    assert e.web_status()["playback_request"] == request


def test_unavailable_device_reports_immediate_failure_without_queuing(launch):
    e, path, pending, _ = launch
    e.active.available.return_value = (False, "Device unavailable")
    reply = e.web_do("cast", {"path": path})
    assert reply["ok"] is False
    assert e.web_status()["playback_request"]["state"] == "failed"
    assert e.web_status()["playback_request"]["error"] == "Device unavailable"
    assert pending == []


def test_superseded_queued_launch_and_callbacks_cannot_overwrite_new_request(launch):
    e, path, pending, _ = launch
    e.cast(path, 10)
    old_start, old_done = pending.pop(0)
    _, old_probe = pending.pop(0)
    first_id = e.web_status()["playback_request"]["id"]
    e.cast(path, 60)
    second_id = e.web_status()["playback_request"]["id"]
    assert second_id != first_id
    old_done(old_start())
    old_probe({"chapters": [{"title": "stale"}]})
    e.active.start.assert_not_called()
    assert e._casting
    assert e._media["chapters"] == []
    fn, done = pending.pop(0)
    done(fn())
    e.active.start.assert_called_once_with(path, [path], False, 60, None, None)
    assert e.web_status()["playback_request"]["id"] == second_id


def test_poll_scheduled_during_launch_cannot_falsely_confirm_new_playback(launch):
    e, path, pending, _ = launch
    e.cast(path)
    _, done = pending.pop(0)
    e.poll_cast()
    _, old_poll = pending.pop()
    done("playing")
    old_poll({"idle": False, "state": "PLAYING", "pos": 10, "dur": 600})
    assert e.web_status()["playback_request"]["state"] == "starting"
    playing(e)
    assert e.web_status()["playback_request"]["state"] == "playing"


def test_unrelated_receiver_cannot_confirm_a_verifiable_cast(launch):
    e, path, pending, _ = launch

    class Sender:
        name = 'Sender'
        def available(self): return True, ''
        def start(self, *_): return 'playing'
        def status(self): return {'idle': True, 'busy': True}
        def sender_matches_content(self, content): return content == 'http://sender/stream'

    e.active = Sender()
    e.cast(path)
    fn, done = pending.pop(0)
    done(fn())
    checkpoint = {'id': 'checkpoint', 'path': path, 'pos': 30}
    e._interrupted_playback = checkpoint
    e._poll_done({'idle': False, 'state': 'PLAYING', 'pos': 50,
                  'content_id': 'http://other/stream'})
    assert e.web_status()['playback_request']['state'] == 'starting'
    assert e._interrupted_playback == checkpoint
    e._poll_done({'idle': False, 'state': 'PLAYING', 'pos': 50,
                  'content_id': 'http://sender/stream'})
    assert e.web_status()['playback_request']['state'] == 'playing'


def test_confirmation_timeout_reports_failure_without_retrying_and_accepts_late_confirmation(launch, monkeypatch):
    e, path, pending, messages = launch
    clock = [100.0]
    monkeypatch.setattr(engine.time, "monotonic", lambda: clock[0])
    e.cast(path)
    fn, done = pending.pop(0)
    done(fn())
    clock[0] += e.PLAYBACK_CONFIRM_TIMEOUT + 1
    e.tick()
    request = e.web_status()["playback_request"]
    assert request["state"] == "failed"
    assert request["error_code"] == "confirmation_timeout"
    assert "not confirmed" in messages[-1]
    e.tick()
    e.active.start.assert_called_once()
    playing(e)
    assert e.web_status()["playback_request"]["state"] == "playing"
    assert e.web_status()["playback_request"]["error"] is None


@pytest.mark.parametrize("action", ["stop", "shutdown", "target"])
def test_cancelled_pending_launch_cannot_run_after_controller_change(launch, action, monkeypatch):
    e, path, pending, _ = launch
    e.cast(path)
    fn, done = pending.pop(0)
    if action == "target":
        e.targets["vlc"] = Mock()
        monkeypatch.setitem(engine.config.CFG, "targets", [{"type": "cast"}, {"type": "vlc"}])
        e.set_target_index(1)
    else:
        getattr(e, action)()
    done(fn())
    assert e.web_status()["playback_request"]["state"] == "failed"
    e.targets["cast"].start.assert_not_called()


def test_transport_stop_invalidates_queued_launch_before_device_worker_runs(launch):
    e, path, pending, _ = launch
    e.cast(path)
    start, done = pending.pop(0)
    e.transport(e.stop)
    assert e.web_status()['playback_request']['state'] == 'failed'
    done(start())
    e.active.start.assert_not_called()


def test_snapshots_are_independent_of_later_request_transitions(launch):
    e, path, pending, _ = launch
    e.cast(path)
    before = e.web_status()["playback_request"]
    pending.pop(0)[1]("Launch failed")
    assert before["state"] == "starting"
    assert e.web_status()["playback_request"]["state"] == "failed"


class FallbackCast:
    name = 'Fake Cast'
    _can_fallback_1080 = True

    def __init__(self):
        self.calls = []

    def available(self):
        return True, ''

    def start_guarded(self, path, items, auto_next, resume, audio, sub, *, current, max_height=None):
        if not current():
            return {'error': 'superseded'}
        self.calls.append((path, resume, audio, sub, max_height))
        return 'playing'

    def status(self):
        return {'idle': True, 'busy': True}

    def owns_buffering_content(self, content):
        return content == 'http://sender/stream'

    def shutdown(self):
        pass


def start_4k_request(launch, monkeypatch):
    e, path, pending, messages = launch
    device = FallbackCast()
    e.active = device
    e.targets['cast'] = device
    clock = [100.0]
    monkeypatch.setattr(engine.time, 'monotonic', lambda: clock[0])
    e.cast(path, 120, audio=1, sub={'i': 2})
    old_id = e.web_status()['playback_request']['id']
    fn, done = pending.pop(0)
    done(fn())
    pending.clear()
    return e, path, pending, messages, device, clock, old_id


def buffering(e, content='http://sender/stream', state='BUFFERING'):
    e._poll_done({'idle': False, 'state': state, 'file': 'pipe_output',
                  'content_id': content, 'pos': 0, 'dur': 0, 'live': True})


def test_4k_buffering_retries_once_at_1080p_with_same_request(launch, monkeypatch):
    e, path, pending, _, device, clock, request_id = start_4k_request(launch, monkeypatch)
    buffering(e)
    clock[0] += e.PLAYBACK_CONFIRM_TIMEOUT
    buffering(e)
    e.tick()
    result = e.web_status()['playback_request']
    assert result['id'] == request_id
    assert result['state'] == 'starting'
    assert result['compatibility_mode'] == '1080p'
    fn, done = pending.pop(0)
    done(fn())
    assert device.calls[-1] == (path, 120, 1, {'i': 2}, 1080)
    pending.clear()
    clock[0] += e.PLAYBACK_CONFIRM_TIMEOUT
    buffering(e)
    e.tick()
    assert e.web_status()['playback_request']['state'] == 'failed'
    assert len(device.calls) == 2
    assert pending == []


def test_4k_fallback_can_report_playing_after_confirmation(launch, monkeypatch):
    e, _, pending, _, _, clock, request_id = start_4k_request(launch, monkeypatch)
    e._poll_done({'idle': True, 'busy': True})  # receiver is still starting
    buffering(e)
    clock[0] += e.PLAYBACK_CONFIRM_TIMEOUT
    buffering(e)
    e.tick()
    fn, done = pending.pop(0)
    done(fn())
    e._poll_done({'idle': False, 'state': 'PLAYING', 'file': 'pipe_output',
                  'content_id': 'http://sender/stream', 'pos': 0, 'dur': 0, 'live': True})
    assert e.web_status()['playback_request']['id'] == request_id
    assert e.web_status()['playback_request']['state'] == 'playing'


def test_confirmed_fallback_is_remembered_for_resume_and_live_seek(launch, monkeypatch):
    e, path, pending, _, device, clock, _ = start_4k_request(launch, monkeypatch)
    buffering(e)
    clock[0] += e.PLAYBACK_CONFIRM_TIMEOUT
    buffering(e)
    e.tick()
    fn, done = pending.pop(0)
    done(fn())
    e._poll_done({'idle': False, 'state': 'PLAYING', 'file': 'pipe_output',
                  'content_id': 'http://sender/stream', 'pos': 5, 'dur': 0, 'live': True})
    assert engine.cast_compatibility.remembered(e._compatibility_key)
    pending.clear()
    e.transport(e.forward)
    assert e.web_status()['playback_request']['compatibility_mode'] == '1080p'
    fn, done = pending.pop(0)
    done(fn())
    assert device.calls[-1] == (path, 155, 1, {'i': 2}, 1080)
    pending.clear()
    e.cast(path, 300)
    assert e.web_status()['playback_request']['compatibility_mode'] == '1080p'
    fn, done = pending.pop(0)
    done(fn())
    assert device.calls[-1][-1] == 1080


def test_fallback_is_not_remembered_from_unrelated_playback(launch, monkeypatch):
    e, _, pending, _, _, clock, _ = start_4k_request(launch, monkeypatch)
    buffering(e)
    clock[0] += e.PLAYBACK_CONFIRM_TIMEOUT
    buffering(e)
    e.tick()
    fn, done = pending.pop(0)
    done(fn())
    e._poll_done({'idle': False, 'state': 'PLAYING', 'file': 'pipe_output',
                  'content_id': 'http://other/stream', 'pos': 0, 'dur': 0, 'live': True})
    assert not engine.cast_compatibility.remembered(e._compatibility_key)
    assert e.web_status()['playback_request']['state'] == 'starting'


@pytest.mark.parametrize('bad_status', [
    {'file': 'pipe_output'},
    {'file': 'pipe_output', 'content_id': 'http://other/stream'},
    {'file': 'different.mp4', 'content_id': 'http://sender/stream'},
])
def test_unrelated_receiver_cannot_confirm_1080p_retry_or_clear_checkpoint(launch, monkeypatch, bad_status):
    e, _, pending, _, _, clock, _ = start_4k_request(launch, monkeypatch)
    buffering(e)
    clock[0] += e.PLAYBACK_CONFIRM_TIMEOUT
    buffering(e)
    e.tick()
    fn, done = pending.pop(0)
    done(fn())
    checkpoint = {'id': 'checkpoint', 'path': '/fake', 'pos': 120}
    e._interrupted_playback = checkpoint
    e._poll_done({'idle': False, 'state': 'PLAYING', 'pos': 5, 'dur': 0,
                  'live': True, **bad_status})
    assert e.web_status()['playback_request']['state'] == 'starting'
    assert e._interrupted_playback == checkpoint
    assert not engine.cast_compatibility.remembered(e._compatibility_key)


@pytest.mark.parametrize('change', ['different_content', 'unreachable', 'paused', 'no_content',
                                    'one_poll', 'stale', 'user_action', 'not_4k'])
def test_fallback_does_not_replace_uncertain_or_changed_session(launch, monkeypatch, change):
    e, _, pending, _, device, clock, _ = start_4k_request(launch, monkeypatch)
    buffering(e)
    if change == 'different_content': buffering(e, content='http://other/stream')
    elif change == 'unreachable': e._poll_done({'idle': True, 'busy': True, 'error': 'offline'})
    elif change == 'paused': buffering(e, state='PAUSED')
    elif change == 'no_content': buffering(e, content='')
    elif change == 'user_action':
        e.transport(lambda: None)
        pending.clear()
    elif change == 'not_4k': device._can_fallback_1080 = False
    clock[0] += e.PLAYBACK_CONFIRM_TIMEOUT
    if change not in ('different_content', 'unreachable', 'paused', 'no_content', 'one_poll', 'stale'):
        buffering(e)
    e.tick()
    assert e.web_status()['playback_request']['state'] == 'failed'
    assert len(device.calls) == 1
    assert pending == []
