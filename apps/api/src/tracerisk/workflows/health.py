"""Local, non-secret readiness evidence for the combined workflow process."""

import argparse
import json
import os
import time
from pathlib import Path

HEALTH_PATH = Path("/tmp/tracerisk-worker-health.json")
MAX_AGE_SECONDS = 15


class HealthReporter:
    def __init__(self, path: Path = HEALTH_PATH) -> None:
        self.path = path
        self.state: dict[str, int | float | None] = {
            "pid": os.getpid(),
            "dispatcher_scan": None,
            "worker_queue": None,
        }
        self._write()

    def _write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.state, sort_keys=True), encoding="utf-8")
        os.replace(temporary, self.path)

    def mark_dispatcher(self) -> None:
        self.state["dispatcher_scan"] = time.time()
        self._write()

    def mark_worker(self) -> None:
        self.state["worker_queue"] = time.time()
        self._write()

    def close(self) -> None:
        self.path.unlink(missing_ok=True)


def is_healthy(path: Path = HEALTH_PATH, *, now: float | None = None) -> bool:
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        if state["pid"] != 1:
            os.kill(int(state["pid"]), 0)
        current = time.time() if now is None else now
        return all(
            isinstance(state[key], (float, int))
            and 0 <= current - state[key] <= MAX_AGE_SECONDS
            for key in ("dispatcher_scan", "worker_queue")
        )
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return False


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", required=True)
    parser.parse_args()
    raise SystemExit(0 if is_healthy() else 1)
