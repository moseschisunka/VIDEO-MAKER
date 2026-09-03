"""Run the PR-1009 offline bounded load/soak probes.

Usage::

    python scripts/measure_load_soak.py --output pr1009-load-soak.json

The command never contacts a provider, downloads an asset, or spends money.
The output is suitable for attaching to the production-readiness evidence
record and is intentionally JSON rather than a human-only summary.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from lib.load_harness import run_all_probes  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Measure PR-1009 offline load and soak gates")
    parser.add_argument("--output", type=Path, help="write JSON evidence to this path")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--iterations", type=int, default=10)
    args = parser.parse_args()
    if args.workers < 1 or args.workers > 4:
        parser.error("--workers must be between 1 and 4")
    if args.iterations < 1 or args.iterations > 100:
        parser.error("--iterations must be between 1 and 100")

    scratch = Path(tempfile.mkdtemp(prefix="openmontage-pr1009-"))
    try:
        evidence = run_all_probes(scratch, workers=args.workers, iterations=args.iterations)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)

    encoded = json.dumps(evidence, indent=2, sort_keys=True) + "\n"
    if args.output:
        destination = args.output.expanduser().resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    return 0 if evidence["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
