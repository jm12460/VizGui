"""Pick the OS integration backend: cross-platform defaults from ``base``, then
the matching platform module on top. Code elsewhere imports this package and
calls e.g. ``platforms.mount_share(...)`` / ``platforms.config_dir()``.
"""
import sys

from .base import *          # noqa: F401,F403 - defaults for everything

if sys.platform.startswith("linux"):
    from .linux import *     # noqa: F401,F403
elif sys.platform == "darwin":
    try:
        from .macos import *  # noqa: F401,F403
    except ImportError:
        pass                  # not implemented yet -> base defaults
elif sys.platform.startswith("win"):
    try:
        from .windows import *  # noqa: F401,F403
    except ImportError:
        pass
