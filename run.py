"""One command from a fresh clone: python3 run.py

Creates .venv, installs Portorium, and opens the console in sample mode, which replays the recorded
responses of NVIDIA, Nightshift and Zetaris and runs the gate's own logic on them. No keys needed.

  python3 run.py                          the site and console at http://127.0.0.1:8781
  python3 run.py classify "<product>"     the command line, same sample mode
  python3 run.py test                     the test suite
  python3 run.py --live                   live mode (needs .env, Nightshift and Zetaris; see the README)
"""
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


def main(args: list[str]) -> int:
    if sys.version_info < (3, 12):
        uv = shutil.which("uv")
        if uv:
            # uv fetches a Python 3.12 if none is installed, then runs this same script with it.
            return subprocess.run([uv, "run", "--no-project", "--python", "3.12", __file__, *args]).returncode
        print(f"Portorium needs Python 3.12 or newer; this is {sys.version.split()[0]}.\n"
              "Install it from python.org (or `brew install python@3.12`) and run `python3.12 run.py`, "
              "or install uv (https://docs.astral.sh/uv/) and run `uv run --python 3.12 run.py`.")
        return 1
    python = BIN / ("python.exe" if os.name == "nt" else "python")
    if not python.exists():
        print("Creating .venv ...")
        venv.create(VENV, with_pip=True)
    if subprocess.run([str(python), "-c", "import portorium.web, pytest"], cwd=ROOT, capture_output=True).returncode:
        print("Installing Portorium (about a minute) ...")
        subprocess.run([str(python), "-m", "pip", "install", "--quiet", "--disable-pip-version-check", "-e", ".[dev]"],
                       cwd=ROOT, check=True)
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
