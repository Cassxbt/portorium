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
import subprocess
import sys
import venv
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"
BIN = VENV / ("Scripts" if os.name == "nt" else "bin")
PORT = 8781


def setup() -> Path | None:
    """The .venv's Python with Portorium installed; uv when available (it fetches Python 3.12 if needed)."""
    python = BIN / ("python.exe" if os.name == "nt" else "python")
    uv = shutil.which("uv")
    if not python.exists():
        print("Creating .venv ...")
        if uv:
            subprocess.run([uv, "venv", "--quiet", "--python", "3.12", str(VENV)], cwd=ROOT, check=True)
        elif sys.version_info >= (3, 12):
            venv.create(VENV, with_pip=True)
        else:
            print(f"Portorium needs Python 3.12 or newer; this is {sys.version.split()[0]}.\n"
                  "Install it from python.org (or `brew install python@3.12`) and run `python3.12 run.py`,\n"
                  "or install uv (https://docs.astral.sh/uv/) and run `python3 run.py` again.")
            return None
    if subprocess.run([str(python), "-c", "import portorium.web, pytest"], cwd=ROOT, capture_output=True).returncode:
        print("Installing Portorium (about a minute) ...")
        install = ([uv, "pip", "install", "--quiet", "--python", str(python)] if uv
                   else [str(python), "-m", "pip", "install", "--quiet", "--disable-pip-version-check"])
        subprocess.run([*install, "-e", ".[dev]"], cwd=ROOT, check=True)
    return python


def main(args: list[str]) -> int:
    python = setup()
    if python is None:
        return 1
    live = "--live" in args
    args = [a for a in args if a != "--live"]
    env = {**os.environ, "SAMPLE_MODE": "false" if live else "true"}
    if args[:1] == ["test"]:
        return subprocess.run([str(python), "-m", "pytest", "-q", *args[1:]], cwd=ROOT, env=env).returncode
    if args:
        return subprocess.run([str(python), "-m", "portorium.cli", *args], cwd=ROOT, env=env).returncode
    url = f"http://127.0.0.1:{PORT}"
    print(f"Portorium ({'live' if live else 'sample mode, recorded responses'}) at {url}. Ctrl+C to stop.")
    webbrowser.open(url)
    try:
        return subprocess.run([str(python), "-m", "uvicorn", "portorium.web:app", "--port", str(PORT), "--log-level", "warning"],
                              cwd=ROOT, env=env).returncode
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
