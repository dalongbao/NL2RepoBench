"""Select a generation backend and execute the configured benchmark batch."""

import argparse
import json
import os
from pathlib import Path

from logging_config import get_logger
import test_data_service

logger = get_logger(__name__)
BACKENDS = ("openhands", "deepseek")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=str(Path(__file__).parent / "config.json"))
    parser.add_argument("--backend", choices=BACKENDS)
    parser.add_argument("--skip-evaluation", action="store_true", help="Generate repositories without running benchmark tests")
    args = parser.parse_args()
    with Path(args.config).expanduser().open(encoding="utf-8") as stream:
        config = json.load(stream)
    if not isinstance(config, dict):
        parser.error("Configuration must be a JSON object")
    if args.skip_evaluation:
        config["evaluate"] = False
    backend = args.backend or config.get("backend", "openhands")
    if backend not in BACKENDS:
        parser.error(f"Unknown backend: {backend}")
    # Existing task metadata and evaluator paths are relative to the benchmark checkout.
    os.chdir(Path(__file__).resolve().parent)
    test_data_service.read_all_test_data()
    if backend == "deepseek":
        from deepseek.runner import start_deepseek
        results = start_deepseek(config)
    else:
        from openhands.openhands_app import start_openhands
        results = start_openhands(config)
    failed = sum(result["status"] != "completed" for result in results)
    logger.info("%s batch finished: %d tasks, %d failed", backend, len(results), failed)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
