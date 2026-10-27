"""OS-integration contract + cross-platform default implementations.

Each supported OS overrides the specifics in its own module (``linux.py``, and
later ``windows.py`` / ``macos.py``); ``__init__.py`` loads these defaults first,
then the matching platform module on top. Anything not overridden falls back here.
"""
import os
import shutil
import subprocess

APP = "vizgui"


def _home(*parts):
    return os.path.join(os.path.expanduser("~"), *parts)


def config_dir():
    """Where ``config.json`` lives (persistent settings + secrets)."""
    return _home(".config", APP)


def cache_dir():
    """Where served media (safe-named symlinks / remuxes) is staged."""
    return _home(".cache", "cast")


def log_dir():
    """Logs, the live-transcode wrapper, and caster output."""
    return _home(".cache", "viz-gui")


def find_tool(name, fallbacks=()):
    """Locate an external executable: PATH first, then any fallback paths, else
    the bare name (so the eventual call fails with a clear 'not found')."""
    p = shutil.which(name)
    if p:
        return p
    for f in fallbacks:
        f = os.path.expanduser(f)
        if os.path.isfile(f):
            return f
    return name


def has_tool(name, fallbacks=()):
    """True if an external executable is available (on PATH or a fallback path)."""
    if shutil.which(name):
        return True
    return any(os.path.isfile(os.path.expanduser(f)) for f in fallbacks)


def mount_share(server, share, user="", domain="", password="", guest=False, timeout=30):
    """Mount an SMB share and return a local path. Overridden per-OS."""
    raise NotImplementedError("Mounting SMB shares is not supported on this platform")


def find_mount(server, share):
    """Return the local path of an already-mounted smb://server/share, or None."""
    return None


def lan_cidr(ip):
    """Network CIDR for a local IP. Generic fallback assumes a /24."""
    return ".".join(ip.split(".")[:3]) + ".0/24"


def firewall_allow_cmd(cidr, port):
    """A command the user can run to allow the phone-remote port on the LAN, or
    None when we don't know this platform's firewall."""
    return None


def supports_mpris():
    """True if a manually-opened media player can be controlled over MPRIS/D-Bus."""
    return False


# ---- process launching (OS-specific: PTY, sessions, process-group kill) ----
class CasterHandle:
    """Popen-like handle for the media-server child: ``.pid`` / ``.returncode`` /
    ``.poll()`` / ``.wait()`` / ``.terminate()`` (terminate kills the whole tree)."""
    def __init__(self, proc):
        self._p = proc

    @property
    def pid(self):
        return self._p.pid

    @property
    def returncode(self):
        return self._p.returncode

    def poll(self):
        return self._p.poll()

    def wait(self, timeout=None):
        return self._p.wait(timeout)

    def terminate(self):
        p = self._p
        if p.poll() is None:
            try:
                p.terminate()
                p.wait(timeout=3)
            except Exception:
                try:
                    p.kill()
                except Exception:
                    pass


def launch_caster(args, env=None, log_path=None):
    """Launch the media-server child (go-chromecast) and return a CasterHandle.
    Default: a plain detached process; platforms that need a controlling terminal
    (Linux) override this."""
    out = open(log_path, "wb") if log_path else subprocess.DEVNULL
    p = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=out, stderr=out, env=env)
    if log_path:
        out.close()
    return CasterHandle(p)


def launch_detached(args, log_path=None):
    """Launch a process that keeps running after we exit (e.g. VLC). Returns the
    Popen; control it with ``.terminate()`` / ``.kill()``."""
    out = open(log_path, "wb") if log_path else subprocess.DEVNULL
    p = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=out, stderr=out)
    if log_path:
        out.close()
    return p


def hw_encode_args():
    """``(input_prefix, video_codec_args)`` for a hardware H.264 encoder on this
    platform (as ffmpeg arg strings), or None to fall back to software libx264."""
    return None
