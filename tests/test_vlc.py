import types
from unittest import mock

from vizgui import vlc


def test_uri_roundtrip():
    p = "/tank/media/TV/Rick & Morty (2013)/ep +1.mkv"
    assert vlc.uri_to_path(vlc.path_to_uri(p)) == p


def test_vlc_status_parse():
    xml = ("<root><time>33</time><length>1900</length><state>playing</state>"
           "<information><category><info name='filename'>Foo Bar.mkv</info>"
           "</category></information></root>")
    with mock.patch.object(vlc, "vlc_req", return_value=xml):
        d = vlc.vlc_status()
    assert d["state"] == "PLAYING"
    assert d["pos"] == 33 and d["dur"] == 1900
    assert d["file"] == "Foo Bar.mkv"


def test_vlc_status_error():
    with mock.patch.object(vlc, "vlc_req", side_effect=OSError("refused")):
        assert "error" in vlc.vlc_status()


def test_mpris_status_parse():
    def fake_pctl(*args, timeout=4):
        r = types.SimpleNamespace(returncode=0, stdout="", stderr="")
        if args[0] == "status":
            r.stdout = "Playing\n"
        elif args[0] == "position":
            r.stdout = "73.5\n"
        elif args[0] == "metadata":
            r.stdout = "1312000000\tEp Title\tfile:///p/ep%20title.mkv\n"
        return r
    with mock.patch.object(vlc, "_pctl", fake_pctl):
        d = vlc.mpris_status()
    assert d["state"] == "PLAYING"
    assert d["pos"] == 73 and d["dur"] == 1312
    assert d["file"] == "Ep Title"
    assert d["path"] == "/p/ep title.mkv"


def test_mpris_status_no_player():
    def fake_pctl(*args, timeout=4):
        return types.SimpleNamespace(returncode=1, stdout="", stderr="No players found")
    with mock.patch.object(vlc, "_pctl", fake_pctl):
        assert "error" in vlc.mpris_status()


def test_vlc_any_status_prefers_http():
    with mock.patch.object(vlc, "vlc_status", return_value={"state": "PLAYING"}):
        s = vlc.vlc_any_status()
    assert s["_mode"] == "http"


def test_vlc_any_status_falls_back_to_mpris():
    with mock.patch.object(vlc, "vlc_status", return_value={"error": "x"}), \
         mock.patch.object(vlc, "mpris_status", return_value={"state": "PLAYING"}):
        s = vlc.vlc_any_status()
    assert s["_mode"] == "mpris"
