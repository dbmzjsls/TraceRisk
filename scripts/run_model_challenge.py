"""Run the staged, opt-in real-model challenge evaluator."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/api/src"))

from tracerisk.challenge_eval.runner import main

if __name__ == "__main__":
    raise SystemExit(main(root=ROOT))
