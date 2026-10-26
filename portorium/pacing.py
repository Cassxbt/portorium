import threading
import time


class Pacer:
    """Spaces calls under a provider's rate cap, shared across threads.

    The interval adapts to the provider's rate limit: each 429 widens it, each success narrows it back
    toward the floor. Timeouts and server errors are retried by the caller but do not slow every other
    request down, and the ceiling is low enough that one bad minute cannot stall a long run.
    """

    def __init__(self, per_minute: int, ceiling_seconds: float = 8.0):
        self.floor = 60.0 / per_minute
        self.ceiling = ceiling_seconds
        self.interval = self.floor
        self._lock = threading.Lock()
        self._next = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            start = max(now, self._next)
            self._next = start + self.interval
        time.sleep(max(0.0, start - now))

    def rejected(self) -> None:
        with self._lock:
            self.interval = min(self.ceiling, self.interval * 1.5)

    def succeeded(self) -> None:
        with self._lock:
            self.interval = max(self.floor, self.interval * 0.85)


# Backoff for transient failures; the NVIDIA free-tier cap resets each minute, so the tail reaches past 60 s.
BACKOFF_SECONDS = (2, 5, 15, 30, 65)


# Nightshift's NVIDIA lanes and the direct embed/rerank calls spend the same NVIDIA key, whose
# per-minute cap applies to their sum, so every caller shares one pacer.
NVIDIA_KEY = Pacer(per_minute=36)
