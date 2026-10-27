import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
RECORDINGS = DATA / "recordings"
SAMPLE = DATA / "sample"

DEFAULT_TOKEN_FILE = Path.home() / "Library/Application Support/ai.meterless.nightshift.m1m4/runtime-token"


def load_dotenv(path: Path = ROOT / ".env") -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


@dataclass(frozen=True)
class Settings:
    nvidia_api_key: str
    nightshift_url: str
    nightshift_token: str
    zetaris_url: str
    zetaris_username: str
    zetaris_password: str
    zetaris_org_id: str
    sample_mode: bool

    @property
    def live(self) -> bool:
        """Sample mode runs the real gate on recorded service responses; live mode needs every service."""
        return not self.sample_mode and all(self._credentials().values())

    def _credentials(self) -> dict[str, str]:
        return {"NVIDIA_API_KEY": self.nvidia_api_key, "NIGHTSHIFT token (NIGHTSHIFT_TOKEN_FILE)": self.nightshift_token,
                "ZETARIS_USERNAME": self.zetaris_username, "ZETARIS_PASSWORD": self.zetaris_password}

    def check(self) -> None:
        """No credentials means sample mode; some but not all means a live set-up that is incomplete, which
        must fail rather than quietly replay recordings."""
        missing = [name for name, value in self._credentials().items() if not value]
        if not self.sample_mode and 0 < len(missing) < len(self._credentials()):
            raise RuntimeError(f"live mode is missing {', '.join(missing)}; set them, or SAMPLE_MODE=true to replay")


def settings() -> Settings:
    load_dotenv()
    token_file = Path(os.environ.get("NIGHTSHIFT_TOKEN_FILE") or DEFAULT_TOKEN_FILE)
    token = token_file.read_text().strip() if token_file.exists() else ""
    return Settings(
        nvidia_api_key=os.environ.get("NVIDIA_API_KEY", ""),
        nightshift_url=os.environ.get("NIGHTSHIFT_URL", "http://127.0.0.1:8088/v1").rstrip("/"),
        nightshift_token=token,
        zetaris_url=os.environ.get("ZETARIS_URL", "http://localhost:3000").rstrip("/"),
        zetaris_username=os.environ.get("ZETARIS_USERNAME", ""),
        zetaris_password=os.environ.get("ZETARIS_PASSWORD", ""),
        zetaris_org_id=os.environ.get("ZETARIS_ORG_ID", "1"),
        sample_mode=os.environ.get("SAMPLE_MODE", "").lower() in ("1", "true", "yes"),
    )


def recordings(live: bool) -> Path:
    """Live runs record locally; sample mode replays the committed bundle that scripts/build_sample.py writes."""
    if live and os.environ.get("RECORD_SAMPLE") != "1":
        return RECORDINGS
    return SAMPLE / "recordings"
