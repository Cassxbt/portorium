"""One command from a fresh clone: python3 run.py

Creates .venv, installs Portorium, and opens the console in sample mode, which replays the recorded
responses of NVIDIA, Nightshift and Zetaris and runs the gate's own logic on them. No keys needed.

  python3 run.py                          the site and console at http://127.0.0.1:8781
  python3 run.py classify "<product>"     the command line, same sample mode
  python3 run.py test                     the test suite
  python3 run.py --live                   live mode (needs .env, Nightshift and Zetaris; see the README)
"""
from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import time
import venv
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"
BIN = VENV / ("Scripts" if os.name == "nt" else "bin")
PORT = 8781


def uv_path() -> str | None:
    """uv on PATH, or installed for this user with pip (its wheels run on Python 3.9 and fetch a 3.12)."""
    found = shutil.which("uv")
    if found or sys.version_info >= (3, 12):
        return found
    print("Installing uv to fetch Python 3.12 (pip install --user uv) ...")
    if subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", "--user", "uv"]).returncode:
        return None
    probe = subprocess.run([sys.executable, "-m", "uv", "--version"], capture_output=True)
    return f"{sys.executable} -m uv" if probe.returncode == 0 else None


def setup() -> Path | None:
    """The .venv's Python with Portorium installed; uv when available (it fetches Python 3.12 if needed)."""
    python = BIN / ("python.exe" if os.name == "nt" else "python")
    uv = uv_path()
    uv_cmd = uv.split(" ") if uv else None
    if not python.exists():
        print("Creating .venv ...")
        try:
            if uv_cmd:
                subprocess.run([*uv_cmd, "venv", "--quiet", "--python", "3.12", str(VENV)], cwd=ROOT, check=True)
            elif sys.version_info >= (3, 12):
                venv.create(VENV, with_pip=True, symlinks=os.name != "nt")
            else:
                print(f"Portorium needs Python 3.12 or newer; this is {sys.version.split()[0]}.\n"
                      "Install it from python.org (or `brew install python@3.12`) and run `python3.12 run.py`.")
                return None
        except Exception:
            # A half-made .venv would make every later run skip creation and fail; start clean next time.
            shutil.rmtree(VENV, ignore_errors=True)
            raise
    if subprocess.run([str(python), "-c", "import portorium.web, pytest"], cwd=ROOT, capture_output=True).returncode:
        print("Installing Portorium (about a minute) ...")
        install = ([*uv_cmd, "pip", "install", "--quiet", "--python", str(python)] if uv_cmd
                   else [str(python), "-m", "pip", "install", "--quiet", "--disable-pip-version-check"])
        subprocess.run([*install, "-e", ".[dev]"], cwd=ROOT, check=True)
    return python


def main(args: list[str]) -> int:
    python = setup()
    if python is None:
        return 1
    live = "--live" in args
    args = [a for a in args if a != "--live"]
    # UTF-8 mode: the stored ruling texts are UTF-8, and Windows would otherwise read them as cp1252.
    env = {**os.environ, "SAMPLE_MODE": "false" if live else "true", "PYTHONUTF8": "1"}
    if live:
        check = "from portorium.config import settings; print(settings().live)"
        if subprocess.run([str(python), "-c", check], cwd=ROOT, env=env, capture_output=True, text=True).stdout.strip() != "True":
            print("Live mode needs NVIDIA_API_KEY and Zetaris credentials in .env and Nightshift running; see the README.")
            return 1
    if args[:1] == ["test"]:
        return subprocess.run([str(python), "-m", "pytest", "-q", *args[1:]], cwd=ROOT, env=env).returncode
    if args:
        return subprocess.run([str(python), "-m", "portorium.cli", *args], cwd=ROOT, env=env).returncode
    url = f"http://127.0.0.1:{PORT}"
    server = subprocess.Popen([str(python), "-m", "uvicorn", "portorium.web:app", "--port", str(PORT), "--log-level", "warning"],
                              cwd=ROOT, env=env)
    try:
        for _ in range(60):
            if server.poll() is not None:
                return server.returncode
            with socket.socket() as probe:
                if probe.connect_ex(("127.0.0.1", PORT)) == 0:
                    break
            time.sleep(0.5)
        print(f"Portorium ({'live' if live else 'sample mode, recorded responses'}) at {url}. Ctrl+C to stop.")
        webbrowser.open(url)
        return server.wait()
    except KeyboardInterrupt:
        server.terminate()
        return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
