import os
import tempfile
import types
from unittest import mock

import pytest

from vizgui.platforms import linux, base


def test_linux_lan_cidr_reads_real_prefix():
    out = "2: enp8s0    inet 192.168.0.42/23 brd 192.168.1.255 scope global enp8s0\n"
    with mock.patch.object(linux.subprocess, "run",
                           return_value=types.SimpleNamespace(stdout=out, returncode=0)):
        assert linux.lan_cidr("192.168.0.42") == "192.168.0.0/23"


def test_linux_lan_cidr_fallback_to_24():
    with mock.patch.object(linux.subprocess, "run", side_effect=OSError):
        assert linux.lan_cidr("192.168.7.9") == "192.168.7.0/24"


def test_linux_firewall_cmd_ufw():
    with mock.patch.object(linux, "_svc_active", lambda s: s == "ufw"):
        assert linux.firewall_allow_cmd("10.0.0.0/24", 8950) == \
            "sudo ufw allow from 10.0.0.0/24 to any port 8950 proto tcp"


def test_linux_firewall_cmd_firewalld():
    with mock.patch.object(linux, "_svc_active", lambda s: s == "firewalld"):
        cmd = linux.firewall_allow_cmd("10.0.0.0/24", 8950)
    assert cmd.startswith("sudo firewall-cmd --permanent --add-rich-rule=")
    assert 'source address="10.0.0.0/24"' in cmd and 'port port="8950"' in cmd
    assert cmd.endswith("&& sudo firewall-cmd --reload")


def test_linux_firewall_cmd_none_when_no_firewall():
    with mock.patch.object(linux, "_svc_active", lambda s: False):
        assert linux.firewall_allow_cmd("10.0.0.0/24", 8950) is None


def test_linux_config_dir_respects_xdg():
    with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": "/tmp/xdgcfg"}):
        assert linux.config_dir() == "/tmp/xdgcfg/vizgui"


def test_linux_config_dir_default():
    env = {k: v for k, v in os.environ.items() if k != "XDG_CONFIG_HOME"}
    with mock.patch.dict(os.environ, env, clear=True):
        assert linux.config_dir().endswith("/.config/vizgui")


def test_linux_supports_mpris_true():
    assert linux.supports_mpris() is True


def test_linux_find_mount_none_without_gvfs():
    with mock.patch.object(linux, "_gvfs_dir", return_value="/no/such/gvfs"):
        assert linux.find_mount("srv", "sh") is None


def test_linux_find_mount_handles_url_encoded_spaces():
    with mock.patch.object(linux, "_gvfs_dir", return_value="/tmp/fakegvfs"), \
         mock.patch.object(linux.os, "listdir", return_value=["smb-share:server=nas,share=all%20videos"]), \
         mock.patch.object(linux.os.path, "isdir", return_value=True):
        assert linux.find_mount("nas", "all Videos") == "/tmp/fakegvfs/smb-share:server=nas,share=all%20videos"


def test_base_find_tool_prefers_path():
    with mock.patch.object(base.shutil, "which", return_value="/usr/bin/ffmpeg"):
        assert base.find_tool("ffmpeg") == "/usr/bin/ffmpeg"


def test_base_find_tool_uses_fallback():
    with tempfile.TemporaryDirectory() as d:
        tool = os.path.join(d, "mytool")
        open(tool, "w").close()
        with mock.patch.object(base.shutil, "which", return_value=None):
            assert base.find_tool("mytool", [tool]) == tool


def test_base_find_tool_bare_name_when_missing():
    with mock.patch.object(base.shutil, "which", return_value=None):
        assert base.find_tool("definitely-not-a-tool", []) == "definitely-not-a-tool"


def test_base_defaults():
    assert base.supports_mpris() is False
    assert base.find_mount("a", "b") is None
    assert base.firewall_allow_cmd("10.0.0.0/24", 80) is None
    assert base.lan_cidr("172.16.5.9") == "172.16.5.0/24"


@pytest.fixture
def fresh_linux_hw(monkeypatch):
    """Reset the per-process encoder cache and any developer override."""
    monkeypatch.delenv("VIZGUI_HW_ENCODER", raising=False)
    monkeypatch.setattr(linux, "_HW_CACHE", linux._HW_UNSET)
    monkeypatch.setattr(linux, "_render_nodes", lambda: [])
    return monkeypatch


def _probe_result(working):
    """Fake subprocess.run: a probe succeeds only if its encoder is in `working`."""
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        enc = cmd[cmd.index("-c:v") + 1]
        node = cmd[cmd.index("-vaapi_device") + 1] if "-vaapi_device" in cmd else ""
        ok = enc in working or f"{enc}@{node}" in working
        return types.SimpleNamespace(returncode=0 if ok else 1, stdout="",
                                     stderr="" if ok else "Cannot load libcuda.so.1\n")
    return run, calls


def test_linux_hw_encode_uses_nvenc_when_test_encode_works(fresh_linux_hw):
    run, calls = _probe_result({"h264_nvenc"})
    fresh_linux_hw.setattr(linux.subprocess, "run", run)
    a = linux.hw_encode_args()
    assert a[0] == "" and "h264_nvenc" in a[1] and "-pix_fmt yuv420p" in a[1]
    assert a[2] == ""                          # no upload filter for NVENC
    assert calls[0][-3:] == ["-f", "null", "-"]  # a real encode, not -encoders


def test_linux_listed_but_unusable_nvenc_falls_to_vaapi(fresh_linux_hw):
    # Distro ffmpeg builds list h264_nvenc everywhere; on AMD the probe fails.
    run, calls = _probe_result({"h264_vaapi"})
    fresh_linux_hw.setattr(linux.subprocess, "run", run)
    fresh_linux_hw.setattr(linux, "_render_nodes", lambda: ["/dev/dri/renderD128"])
    a = linux.hw_encode_args()
    assert a == ("-vaapi_device /dev/dri/renderD128", linux._VAAPI_CODEC,
                 "format=nv12,hwupload")
    assert [c[c.index("-c:v") + 1] for c in calls] == ["h264_nvenc", "h264_vaapi"]
    assert "format=nv12,hwupload" in calls[1]   # probe uses the real filter chain


def test_linux_vaapi_tries_each_render_node(fresh_linux_hw):
    run, calls = _probe_result({"h264_vaapi@/dev/dri/renderD129"})
    fresh_linux_hw.setattr(linux.subprocess, "run", run)
    fresh_linux_hw.setattr(linux, "_render_nodes",
                           lambda: ["/dev/dri/renderD128", "/dev/dri/renderD129"])
    assert linux.hw_encode_args()[0] == "-vaapi_device /dev/dri/renderD129"
    assert len(calls) == 3


def test_linux_no_working_hw_encoder_is_none_and_cached(fresh_linux_hw):
    run, calls = _probe_result(set())
    fresh_linux_hw.setattr(linux.subprocess, "run", run)
    fresh_linux_hw.setattr(linux, "_render_nodes", lambda: ["/dev/dri/renderD128"])
    assert linux.hw_encode_args() is None
    assert linux.hw_encode_args() is None
    assert len(calls) == 2                      # probed once per process


def test_concurrent_callers_wait_for_same_hardware_probe(fresh_linux_hw):
    import threading
    from concurrent.futures import ThreadPoolExecutor, TimeoutError

    started = threading.Event()
    release = threading.Event()
    second_started = threading.Event()
    run, calls = _probe_result({"h264_nvenc"})

    def slow_probe(cmd, **kwargs):
        started.set()
        assert release.wait(5)
        return run(cmd, **kwargs)

    def second_call():
        second_started.set()
        return linux.hw_encode_args()

    fresh_linux_hw.setattr(linux.subprocess, "run", slow_probe)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(linux.hw_encode_args)
        assert started.wait(5)
        second = pool.submit(second_call)
        assert second_started.wait(5)
        try:
            # The probe must remain the sole source of either result.
            with pytest.raises(TimeoutError):
                second.result(timeout=0.2)
        finally:
            release.set()
        assert first.result(timeout=5) == second.result(timeout=5) == linux._NVENC + ("",)
    assert len(calls) == 1


def test_linux_probe_timeout_or_missing_ffmpeg_is_a_failure(fresh_linux_hw):
    for err in (linux.subprocess.TimeoutExpired("ffmpeg", 10), FileNotFoundError("ffmpeg")):
        fresh_linux_hw.setattr(linux, "_HW_CACHE", linux._HW_UNSET)
        fresh_linux_hw.setattr(linux.subprocess, "run", mock.Mock(side_effect=err))
        assert linux.hw_encode_args() is None


def test_linux_hw_encoder_env_none_skips_probing(fresh_linux_hw):
    fresh_linux_hw.setenv("VIZGUI_HW_ENCODER", "none")
    run = mock.Mock()
    fresh_linux_hw.setattr(linux.subprocess, "run", run)
    assert linux.hw_encode_args() is None
    run.assert_not_called()


def test_linux_hw_encoder_env_vaapi_skips_nvenc(fresh_linux_hw):
    fresh_linux_hw.setenv("VIZGUI_HW_ENCODER", "VAAPI")
    run, calls = _probe_result({"h264_nvenc", "h264_vaapi"})
    fresh_linux_hw.setattr(linux.subprocess, "run", run)
    fresh_linux_hw.setattr(linux, "_render_nodes", lambda: ["/dev/dri/renderD128"])
    assert "h264_vaapi" in linux.hw_encode_args()[1]
    assert len(calls) == 1


def test_linux_probe_command_is_valid_for_real_ffmpeg():
    """The probe must not reject every encoder because of a malformed command."""
    import shutil
    import subprocess
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg is not installed")
    encoders = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"],
                              capture_output=True, text=True).stdout
    if "libx264" not in encoders:
        pytest.skip("ffmpeg lacks libx264")
    assert linux._encoder_works("", "-c:v libx264 -preset ultrafast", "format=nv12")
    assert not linux._encoder_works("", "-c:v vizgui_no_such_encoder")


def test_base_hw_encode_args_default_none():
    assert base.hw_encode_args() is None


def test_linux_verifies_listening_socket_ownership(tmp_path):
    proc = tmp_path / 'proc'
    fd = proc / '123' / 'fd'
    fd.mkdir(parents=True)
    net = proc / 'net'
    net.mkdir()
    (fd / '7').symlink_to('socket:[98765]')
    table = net / 'tcp6'
    table.write_text('header\n 0: 00000000000000000000000000000000:3039 '
                     '00000000000000000000000000000000:0000 0A '
                     '00000000:00000000 00:00000000 00000000 1000 0 98765\n')
    assert linux.process_listens_on_port(123, 12345, proc_root=str(proc))
    assert not linux.process_listens_on_port(123, 12346, proc_root=str(proc))
    assert not linux.process_listens_on_port(999, 12345, proc_root=str(proc))
    table.write_text(table.read_text().replace(' 0A ', ' 01 '))
    assert not linux.process_listens_on_port(123, 12345, proc_root=str(proc))


def test_linux_launch_caster_runs_and_terminates():
    import time
    h = linux.launch_caster(["cat"])   # reads pty stdin, stays alive
    try:
        time.sleep(0.3)
        assert h.poll() is None and h.pid > 0
    finally:
        h.terminate()
    time.sleep(0.2)
    assert h.poll() is not None


def test_linux_launch_detached_runs_and_terminates():
    p = linux.launch_detached(["sleep", "5"])
    try:
        assert p.poll() is None
    finally:
        p.terminate()
        p.wait(timeout=3)
    assert p.poll() is not None


def test_base_has_tool_on_path():
    with mock.patch.object(base.shutil, "which", return_value="/usr/bin/x"):
        assert base.has_tool("x") is True


def test_base_has_tool_via_fallback():
    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, "t")
        open(f, "w").close()
        with mock.patch.object(base.shutil, "which", return_value=None):
            assert base.has_tool("t", [f]) is True


def test_base_has_tool_missing():
    with mock.patch.object(base.shutil, "which", return_value=None):
        assert base.has_tool("definitely-not-a-tool", []) is False


def test_base_default_media_dirs_only_existing():
    with mock.patch.object(base.os.path, "isdir", lambda p: p.endswith("Videos")):
        assert [n for n, _ in base.default_media_dirs()] == ["Videos"]


def test_linux_xdg_user_dir_parses_file():
    with tempfile.TemporaryDirectory() as d:
        with open(os.path.join(d, "user-dirs.dirs"), "w") as f:
            f.write('XDG_MUSIC_DIR="$HOME/stuff"\nXDG_VIDEOS_DIR="$HOME/Media/Vids"\n')
        with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": d}):
            p = linux._xdg_user_dir("VIDEOS", "Videos")
    assert p.endswith("/Media/Vids") and "$HOME" not in p


def test_linux_xdg_user_dir_fallback_when_absent():
    with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": "/no/such/dir"}):
        assert linux._xdg_user_dir("VIDEOS", "Videos").endswith("/Videos")


def test_linux_default_media_dirs_only_existing():
    with mock.patch.object(linux, "_xdg_user_dir", lambda key, fb: f"/x/{fb}"), \
         mock.patch.object(linux.os.path, "isdir", lambda p: p == "/x/Videos"):
        assert linux.default_media_dirs() == [("Videos", "/x/Videos")]


def test_base_default_media_dirs_contract_default():
    # the contract default returns a list (whatever exists on the box)
    assert isinstance(base.default_media_dirs(), list)


# ================= Windows backend (pure logic; runs anywhere via mocks) =======
from vizgui.platforms import windows, macos  # noqa: E402


def test_windows_config_dir_uses_appdata():
    with mock.patch.dict(windows.os.environ, {"APPDATA": r"C:\Users\x\AppData\Roaming"}):
        assert windows.config_dir().endswith("vizgui") and "Roaming" in windows.config_dir()


def test_windows_lan_cidr_via_powershell():
    with mock.patch.object(windows.subprocess, "run",
                           return_value=types.SimpleNamespace(stdout="23\n", returncode=0)):
        assert windows.lan_cidr("192.168.1.50") == "192.168.0.0/23"


def test_windows_lan_cidr_fallback():
    with mock.patch.object(windows.subprocess, "run", side_effect=OSError):
        assert windows.lan_cidr("10.1.2.3") == "10.1.2.0/24"


def test_windows_mount_guest_uses_empty_string_arg():
    # guest arg must be "" (not '""', which subprocess would escape to \"\"),
    # and the net-use call must suppress a console window.
    calls = {}

    def fake_run(cmd, **kw):
        calls["cmd"] = cmd
        calls["flags"] = kw.get("creationflags")
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    # isdir False first (not mounted) -> net use -> True (now mounted)
    with mock.patch.object(windows.subprocess, "run", fake_run), \
         mock.patch.object(windows.os.path, "isdir", side_effect=[False, True]):
        windows.mount_share("nas", "media", guest=True)
    assert '""' not in calls["cmd"] and "" in calls["cmd"]
    assert calls["flags"] == windows._CREATE_NO_WINDOW


def test_windows_firewall_cmd_is_netsh():
    cmd = windows.firewall_allow_cmd("10.0.0.0/24", 8950)
    assert cmd.startswith("netsh advfirewall firewall add rule")
    assert "localport=8950" in cmd and "remoteip=10.0.0.0/24" in cmd


def test_windows_hw_encode_prefers_nvenc():
    windows._HW_CACHE = windows._HW_UNSET
    with mock.patch.object(windows.subprocess, "run",
                           return_value=types.SimpleNamespace(stdout="h264_nvenc h264_qsv h264_amf", returncode=0)):
        a = windows.hw_encode_args()
    windows._HW_CACHE = windows._HW_UNSET
    assert a and "h264_nvenc" in a[1]


def test_windows_hw_encode_falls_to_qsv():
    windows._HW_CACHE = windows._HW_UNSET
    with mock.patch.object(windows.subprocess, "run",
                           return_value=types.SimpleNamespace(stdout="has h264_qsv only", returncode=0)):
        a = windows.hw_encode_args()
    windows._HW_CACHE = windows._HW_UNSET
    assert a and "h264_qsv" in a[1]


def test_windows_vlc_exe_from_fallback():
    with mock.patch.object(windows, "find_tool",
                           return_value=r"C:\Program Files\VideoLAN\VLC\vlc.exe"):
        assert windows.vlc_exe().endswith("vlc.exe")


def test_windows_find_mount_unc():
    with mock.patch.object(windows.os.path, "isdir", lambda p: p == r"\\nas\media"):
        assert windows.find_mount("nas", "media") == r"\\nas\media"
        assert windows.find_mount("nas", "other") is None


# ================= macOS backend ===============================================
def test_macos_config_dir_in_library():
    assert macos.config_dir().endswith("Library/Application Support/vizgui")


def test_macos_default_media_dirs_uses_movies():
    with mock.patch.object(macos.os.path, "isdir", lambda p: True):
        assert [n for n, _ in macos.default_media_dirs()] == ["Movies", "Music", "Downloads"]


def test_macos_lan_cidr_parses_ifconfig_hexmask():
    out = "en0: flags=8863\n\tinet 10.0.0.5 netmask 0xffffff00 broadcast 10.0.0.255\n"
    with mock.patch.object(macos.subprocess, "run",
                           return_value=types.SimpleNamespace(stdout=out, returncode=0)):
        assert macos.lan_cidr("10.0.0.5") == "10.0.0.0/24"


def test_macos_lan_cidr_fallback():
    with mock.patch.object(macos.subprocess, "run", side_effect=OSError):
        assert macos.lan_cidr("172.16.3.4") == "172.16.3.0/24"


def test_macos_hw_encode_videotoolbox():
    macos._HW_CACHE = macos._HW_UNSET
    with mock.patch.object(macos.subprocess, "run",
                           return_value=types.SimpleNamespace(stdout="V... h264_videotoolbox VideoToolbox", returncode=0)):
        a = macos.hw_encode_args()
    macos._HW_CACHE = macos._HW_UNSET
    assert a and "h264_videotoolbox" in a[1]


def test_macos_mount_share_via_osascript():
    with mock.patch.object(macos.subprocess, "run",
                           return_value=types.SimpleNamespace(returncode=0, stdout="", stderr="")) as run, \
         mock.patch.object(macos, "find_mount", side_effect=[None, "/Volumes/media"]):
        assert macos.mount_share("nas", "media", user="u", password="p") == "/Volumes/media"
    call = run.call_args.args[0]
    assert call[0] == "osascript" and "mount volume" in call[2] and "smb://" in call[2]


def test_macos_find_mount_matches_server_and_share():
    out = ("/dev/disk1 on / (apfs, local)\n"
           "//guest@nas/media on /Volumes/media (smbfs, nodev, nosuid)\n")
    with mock.patch.object(macos.subprocess, "run",
                           return_value=types.SimpleNamespace(stdout=out, returncode=0)), \
         mock.patch.object(macos.os.path, "isdir", lambda p: p == "/Volumes/media"):
        assert macos.find_mount("nas", "media") == "/Volumes/media"
        # same share name on a different server must NOT match
        assert macos.find_mount("otherbox", "media") is None
