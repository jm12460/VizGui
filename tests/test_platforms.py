import os
import tempfile
import types
from unittest import mock

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


def test_linux_hw_encode_args_detects_nvenc():
    linux._HW_CACHE = linux._HW_UNSET
    with mock.patch.object(linux.subprocess, "run",
                           return_value=types.SimpleNamespace(stdout="V... h264_nvenc NVIDIA\n", returncode=0)):
        a = linux.hw_encode_args()
    linux._HW_CACHE = linux._HW_UNSET
    assert a and a[0] == "-hwaccel cuda" and "h264_nvenc" in a[1]


def test_linux_hw_encode_args_none_without_nvenc():
    linux._HW_CACHE = linux._HW_UNSET
    with mock.patch.object(linux.subprocess, "run",
                           return_value=types.SimpleNamespace(stdout="only libx264 here\n", returncode=0)):
        a = linux.hw_encode_args()
    linux._HW_CACHE = linux._HW_UNSET
    assert a is None


def test_base_hw_encode_args_default_none():
    assert base.hw_encode_args() is None


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
