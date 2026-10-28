import types
from unittest import mock

from vizgui import chromecast


def test_scan_parse():
    out = ('1) device="E60-E3" device_name="Basement Display" '
           'address="10.0.0.105:8009" uuid="abc123"\n'
           '2) device="Chromecast" device_name="Office" '
           'address="10.0.0.9:8009" uuid="def456"\n')
    with mock.patch.object(chromecast.subprocess, "run",
                           return_value=types.SimpleNamespace(stdout=out, returncode=0)):
        devs = chromecast.scan_cast_devices()
    assert len(devs) == 2
    assert devs[0]["name"] == "Basement Display"
    assert devs[0]["addr"] == "10.0.0.105"
    assert devs[0]["cast_port"] == "8009"
    assert devs[1]["addr"] == "10.0.0.9"


def test_scan_empty_on_error():
    with mock.patch.object(chromecast.subprocess, "run", side_effect=OSError):
        assert chromecast.scan_cast_devices() == []


def test_scan_skips_lines_without_address():
    out = 'no devices found\n'
    with mock.patch.object(chromecast.subprocess, "run",
                           return_value=types.SimpleNamespace(stdout=out, returncode=0)):
        assert chromecast.scan_cast_devices() == []


def test_gc_returns_gracefully_without_device():
    # no cast device configured -> don't invoke go-chromecast with a None address
    with mock.patch.object(chromecast.config, "TV_IP", None), \
         mock.patch.object(chromecast.subprocess, "run",
                           side_effect=AssertionError("must not run gc without a device")):
        ok, msg = chromecast.gc("status")
    assert ok is False and "no cast device" in msg
