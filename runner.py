"""Batch scheduling and artifacts shared by generation backends."""

import concurrent.futures
import json
import re
import shutil
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class Job:
    task_uuid: str
    backend: str
    module_name: str
    pro_name: str
    run: Callable[[], dict]


def new_run_id(project: str, model: str) -> str:
    """Make a filesystem and Docker-safe name unique across models and reruns."""
    prefix = re.sub(r"[^a-z0-9-]+", "-", f"{project}-{model}".lower()).strip("-")
    return f"{prefix[:70]}-{uuid.uuid4().hex}"


def positive_int(value, field: str) -> int:
    if type(value) is not int or value < 1:
        raise ValueError(f"{field} must be a positive integer")
    return value


def evaluation_enabled(config: dict) -> bool:
    value = config.get("evaluate", True)
    if type(value) is not bool:
        raise ValueError("evaluate must be true or false")
    return value


def write_json(path: Path, value) -> None:
    """Publish each result atomically on the same filesystem."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def evaluate_result(result: dict, test_data, enabled: bool) -> dict:
    """Apply the existing evaluator to a copy, retaining the generated source."""
    result.update(evaluation_status="skipped", test_score=None, score=None)
    if not enabled or result["status"] != "completed":
        return result

    workspace = Path(result["workspace_path"])
    evaluation_workspace = workspace.parent / "evaluation" / "workspace"
    try:
        # The evaluator has Docker dependencies that generation-only runs do not need.
        from openhands.post_processor import post_process_task

        shutil.copytree(workspace, evaluation_workspace, symlinks=True)
        evaluation = post_process_task(
            result["task_uuid"], str(evaluation_workspace), test_data, logger,
        )
        result["post_process_result"] = evaluation
        result["evaluation_workspace_path"] = str(evaluation_workspace)
        if evaluation["status"] == "success":
            counts = evaluation["pytest_results"]
            score = counts["passed"] if counts["total"] > 0 else 0
            result.update(evaluation_status="completed", test_score=score, score=score)
        else:
            result.update(evaluation_status="error", status="failed")
    except Exception as exc:
        result.update(evaluation_status="error", status="failed", evaluation_error=str(exc))
    return result


def run_batch(jobs: list, max_workers: int, result_dir: str = "result") -> list:
    """Save a result for every submitted job, including generation failures."""
    positive_int(max_workers, "max_pool_size")
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        pending = {executor.submit(job.run): job for job in jobs}
        for future in concurrent.futures.as_completed(pending):
            job = pending[future]
            try:
                result = future.result()
                if not isinstance(result, dict):
                    raise RuntimeError("Backend returned no task result")
            except Exception as exc:
                result = {"status": "error", "generation_status": "error", "error": str(exc)}
            result.update(
                task_uuid=job.task_uuid, backend=job.backend,
                module_name=job.module_name, pro_name=job.pro_name,
                saved_at=time.strftime("%Y-%m-%d %H:%M:%S"),
            )
            result.setdefault("evaluation_status", "skipped")
            result.setdefault("test_score", None)
            result.setdefault("score", None)
            write_json(Path(result_dir) / f"{job.task_uuid}.json", result)
            results.append(result)
            logger.info("%s / %s: %s (score=%s)", job.pro_name, job.module_name,
                        result["status"], result["score"])
    return results
