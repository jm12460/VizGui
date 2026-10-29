from unittest import mock

from vizgui import engine as E


class FakeTarget:
    """A playback target stub (no device I/O)."""
    name = "Fake"

    def __init__(self, avail=(True, ""), native=False, advance=False):
        self._avail = avail
        self._native = native
        self._advance = advance
        self.started = None
        # realistic "playing" status so a synchronous poll (run_sync) after a cast
        # doesn't immediately report idle and clear the just-set state
        self._status = {"idle": False, "state": "PLAYING", "pos": 0, "dur": 100, "file": "x"}

    def available(self):
        return self._avail

    def start(self, path, items, auto_next, resume, audio, sub):
        self.started = (path, items, resume, audio, sub)
        return "playing"

    def status(self):
        return self._status

    def native_skip(self):
        return self._native

    def needs_advance_on_end(self):
        return self._advance

    def shutdown(self):
        pass

    def skip(self, d):
        return True, ""


def make_engine(**target_kw):
    """Engine with a stub target and all config/history writes mocked out."""
    changes = {"n": 0}
    statuses = []
    e = E.Engine(run_async=E.run_sync,
                 on_change=lambda: changes.__setitem__("n", changes["n"] + 1),
                 on_status=statuses.append)
    e.targets = {"cast": FakeTarget(**target_kw), "vlc": FakeTarget(**target_kw)}
    e.active = e.targets["cast"]
    e.target = "cast"
    return e, changes, statuses


CFG = {"targets": [{"type": "cast", "name": "TV", "addr": "1.2.3.4"},
                   {"type": "vlc", "name": "VLC (this PC)"}],
       "sources": [{"name": "Media", "path": "/m"}],
       "web": {"token": "tok", "pin": "123456"},
       "auto_next": True}


def test_web_status_shape():
    e, _, _ = make_engine()
    e.target_index = 1
    with mock.patch.object(E.config, "CFG", CFG), \
         mock.patch.object(E.config, "HOST", None):
        r = e.web_status()
    assert r["ok"] and r["target_index"] == 1
    assert [t["name"] for t in r["targets"]] == ["TV (Cast)", "VLC (this PC)"]
    assert r["sources"][0]["name"] == "Media"
    assert set(r["now"]) == {"title", "pos", "dur", "state", "live"}


def test_pair_lockout_after_five():
    e, _, _ = make_engine()
    with mock.patch.object(E.config, "CFG", CFG):
        for _ in range(5):
            assert e.web_pair("000000")["ok"] is False
        assert e.web_pair("123456") == {"ok": False, "locked": True}   # locked out
    assert e._pair_locked is True


def test_pair_success_resets_fails():
    e, _, _ = make_engine()
    with mock.patch.object(E.config, "CFG", CFG):
        e.web_pair("000000")
        r = e.web_pair("123456")
    assert r == {"ok": True, "token": "tok"} and e._pair_fails == 0


def test_web_history_excludes_current():
    e, _, _ = make_engine()
    e._now_path = "/m/a.mkv"
    with mock.patch.object(E.history, "recent",
                           return_value=[{"path": "/m/a.mkv"}, {"path": "/m/b.mkv"}]):
        items = e.web_history()
    assert [i["path"] for i in items] == ["/m/b.mkv"]


def test_cast_unavailable_sets_message_and_status():
    e, changes, statuses = make_engine(avail=(False, "VLC isn't installed"))
    e.cast("/m/x.mkv")
    assert e._message == "VLC isn't installed"
    assert statuses == ["VLC isn't installed"]
    assert e._now_path == "/m/x.mkv"       # path recorded even though launch refused


def test_cast_launches_and_renders():
    e, changes, _ = make_engine()
    with mock.patch.object(E.history, "set_current"), \
         mock.patch.object(E.probe, "probe", return_value={"audio": [], "subs": [], "chapters": []}), \
         mock.patch.object(E.media, "folder_queue", return_value=["/m/x.mkv"]):
        e.cast("/m/x.mkv")
    assert e._now_path == "/m/x.mkv" and e._casting is False   # run_sync completed
    assert e.active.started[0] == "/m/x.mkv"
    assert changes["n"] > 0


def test_poll_idle_clears_when_not_playing():
    e, _, _ = make_engine()
    e._now_path = "/m/x.mkv"; e.now_title = "x.mkv"
    with mock.patch.object(E.history, "clear_current"):
        e._poll_done({"idle": True, "busy": False})
    assert e._now_path is None and e.now_title is None


def test_poll_idle_kept_while_casting():
    e, _, _ = make_engine()
    e._now_path = "/m/x.mkv"; e._casting = True
    e._poll_done({"idle": True, "busy": False})
    assert e._now_path == "/m/x.mkv"        # mid-launch -> state preserved


def test_poll_playing_sets_state():
    e, _, _ = make_engine()
    e.target = "vlc"; e.active = e.targets["vlc"]; e._now_path = "/m/x.mkv"
    e._poll_done({"idle": False, "state": "PLAYING", "pos": 42, "dur": 100, "file": "x.mkv"})
    assert e._np_pos == 42 and e._np_dur == 100 and e._np_state == "PLAYING"
    assert e._display_title == "x.mkv" and e._saw_playing is True


def test_on_ended_auto_next():
    e, _, _ = make_engine(advance=True)
    e._now_path = "/m/ep1.mkv"; e.auto_next = True; e._np_dur = 100
    with mock.patch.object(E.history, "record"), \
         mock.patch.object(E.history, "clear_current"), \
         mock.patch.object(E.history, "set_current"), \
         mock.patch.object(E.probe, "probe", return_value={"audio": [], "subs": [], "chapters": []}), \
         mock.patch.object(E.media, "folder_queue", return_value=["/m/ep2.mkv"]), \
         mock.patch.object(E.media, "next_file", return_value="/m/ep2.mkv"):
        e.on_ended()
    assert e._now_path == "/m/ep2.mkv"       # advanced to the next episode


def test_on_ended_clears_when_no_next():
    e, _, _ = make_engine(advance=True)
    e._now_path = "/m/ep1.mkv"; e.auto_next = True; e._np_dur = 100
    with mock.patch.object(E.history, "record"), \
         mock.patch.object(E.history, "clear_current"), \
         mock.patch.object(E.media, "next_file", return_value=None):
        e.on_ended()
    assert e._now_path is None and e._media == {"audio": [], "subs": [], "chapters": []}


def test_tick_advances_position():
    e, _, _ = make_engine()
    e._np_playing = True; e._np_dur = 100; e._np_pos = 5
    e.tick()
    assert e._np_pos == 6


def test_tick_caps_at_duration():
    e, _, _ = make_engine()
    e._np_playing = True; e._np_dur = 10; e._np_pos = 10
    e.tick()
    assert e._np_pos == 10                    # clamped


def test_web_do_autonext():
    e, _, _ = make_engine()
    with mock.patch.object(E, "save_config"), mock.patch.object(E.config, "CFG", dict(CFG)):
        e.web_do("autonext", {"on": False})
    assert e.auto_next is False


def test_web_do_cast_rejects_outside_sources():
    e, _, _ = make_engine()
    called = []
    with mock.patch.object(E, "web_path_ok", return_value=False), \
         mock.patch.object(e, "cast", lambda *a, **k: called.append(a)):
        e.web_do("cast", {"path": "/etc/passwd.mp4"})
    assert called == []                       # confinement blocked it


def test_web_do_cast_allows_inside_sources():
    e, _, _ = make_engine()
    called = []
    with mock.patch.object(E, "web_path_ok", return_value=True), \
         mock.patch.object(e, "cast", lambda *a, **k: called.append(a)):
        e.web_do("cast", {"path": "/m/x.mp4", "resume": 30})
    assert called == [("/m/x.mp4", 30)]
