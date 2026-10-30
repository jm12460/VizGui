#!/usr/bin/env python3
"""Check Python/module resolution and print a systemd user unit; never install it."""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile


TEMPLATE = Path(__file__).with_name("vizgui-server.service")


def unit_string(value, *, command=False):
    """Quote a literal systemd value, including specifier/command expansion."""
    if any(c in value for c in "\n\r\0"):
        raise ValueError("Service paths cannot contain newlines or NUL characters")
    value = value.replace("%", "%%")
    if not command:
        # WorkingDirectory is a single literal path, not an argv expression:
        # quotes would become part of the path. A trailing slash protects a
        # directory name ending in whitespace or a line-continuation backslash.
        return value + ("/" if value[-1:].isspace() or value.endswith("\\") else "")
    # The executable (argv[0]) does not undergo environment substitution.
    value = value.replace("\\", "\\\\").replace('"', '\\"')
    return '"' + value + '"'


def render(python, checkout=None):
    # Keep a venv interpreter's symlink: resolving it could select system Python.
    python = os.path.abspath(os.path.expanduser(python))
    unit_string(python, command=True)
    if not os.path.isfile(python) or not os.access(python, os.X_OK):
        raise ValueError(f"Python executable not found or not executable: {python}")
    if checkout is not None:
        checkout = Path(checkout).expanduser().resolve()
        unit_string(str(checkout))
        if not (checkout / "vizgui/server.py").is_file():
            raise ValueError("Checkout must contain vizgui/server.py")

    # Do not import the server: that would load configuration and create logs.
    # Check from outside this checkout unless source mode was explicitly chosen.
    check = (
        "import importlib.util, sys; "
        "spec = importlib.util.find_spec('vizgui.server'); "
        "sys.exit('vizgui.server was not found') if spec is None else print(spec.origin)"
    )
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONHOME")}
    with tempfile.TemporaryDirectory(prefix="vizgui-service-check-") as scratch:
        try:
            result = subprocess.run(
                [python, "-c", check], cwd=checkout or scratch, env=env,
                capture_output=True, text=True, timeout=15,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ValueError(f"Could not run selected Python: {exc}") from exc
    if result.returncode != 0:
        raise ValueError("Selected Python cannot find vizgui.server. Use --checkout PATH "
                         "for a source install, or install VizGui into this Python environment.")
    if checkout is not None and Path(result.stdout.strip()).resolve() != checkout / "vizgui/server.py":
        raise ValueError("Selected Python resolved a different VizGui checkout")

    lines = TEMPLATE.read_text().splitlines()
    output = []
    for line in lines:
        if line.startswith("ExecStart="):
            if checkout is not None:
                output.append("WorkingDirectory=" + unit_string(str(checkout)))
            output.append("ExecStart=" + unit_string(python, command=True)
                          + " -m vizgui.server --no-print-pin")
        else:
            output.append(line)
    return "\n".join(output) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", default=sys.executable,
                        help="Python executable to use (default: this interpreter)")
    parser.add_argument("--checkout", help="source checkout directory; omit for installed packages")
    args = parser.parse_args(argv)
    try:
        unit = render(args.python, args.checkout)
    except ValueError as exc:
        parser.exit(1, f"Cannot generate VizGui service: {exc}\n")
    print(unit, end="")


if __name__ == "__main__":
    main()
