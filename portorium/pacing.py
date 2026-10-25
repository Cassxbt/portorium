import threading
import time


class Pacer:
    """Spaces calls to stay under a provider's per-minute cap, shared across threads."""

    def __init__(self, per_minute: int):
        self.interval = 60.0 / per_minute
        self._lock = threading.Lock()
        self._next = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            start = max(now, self._next)
            self._next = start + self.interval
        time.sleep(max(0.0, start - now))


# Backoff for transient failures; the NVIDIA free-tier cap resets each minute, so the tail reaches past 60 s.
BACKOFF_SECONDS = (2, 5, 15, 30, 65)
