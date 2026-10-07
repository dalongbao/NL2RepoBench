"""Launch the local DeepSeek Harness image once for each benchmark task."""

import json
import os
import shutil
import subprocess
from functools import partial
from pathlib import Path

from docker_self.batch_container import docker_command, run_batch_container
from runner import Job, evaluate_result, evaluation_enabled, model_tasks, new_run_id, positive_int, run_batch
from .config import resolve_model, write_profile
from .session_summary import summarize_sessions

HARNESS_REVISION = "639ed01539"
DEFAULT_IMAGE = f"nl2repobench-deepseek:{HARNESS_REVISION}"
PACKAGE_DIR = Path(__file__).parent


def _image_info(image: str) -> dict:
    """Fail before scheduling when the locally built image is unavailable."""
    try:
        inspected = docker_command(["image", "inspect", image], stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError("DeepSeek image is unavailable. Start Docker and run scripts/build_deepseek.sh.") from exc
    value = json.loads(inspected.stdout)[0]
    revision = (value["Config"].get("Labels") or {}).get("org.nl2repobench.harness-revision", "")
    if not revision.startswith(HARNESS_REVISION):
        raise ValueError(f"DeepSeek image must be built from harness revision {HARNESS_REVISION}")
    return {"tag": image, "id": value["Id"], "harness_revision": revision}


def _run_task(run_id: str, test_data, model: dict, secret_env: dict,
              options: dict, image: dict, evaluate: bool) -> dict:
    run_dir = Path("workspaces").resolve() / run_id
    workspace = run_dir / "workspace"
    workspace.mkdir(parents=True)
    home = run_dir / "dsh-home"
    shutil.copyfile(test_data.md, workspace / "start.md")
    profile = write_profile(home, model, options["teammates"])
    (home / "user").mkdir()
    prompt = (PACKAGE_DIR / "prompt.txt").read_text(encoding="utf-8").format(teammates=options["teammates"])
    (run_dir / "prompt.txt").write_text(prompt, encoding="utf-8")
    env = dict(os.environ, **secret_env)
    # The image already sets the working directory and the harness home, permission, and telemetry variables.
    arguments = [
        "--init",
        "--user", f"{os.getuid()}:{os.getgid()}",
        "--volume", f"{workspace}:/workspace:rw",
        "--volume", f"{home}:/dsh-home:rw",
        "--env", "HOME=/dsh-home/user",
        "--env", "PATH=/dsh-home/user/.local/bin:/usr/local/bin:/usr/local/sbin:/usr/sbin:/usr/bin:/sbin:/bin",
        "--env", "NL2REPO_MODEL_API_KEY",
        "--add-host", "host.docker.internal:host-gateway",
        image["id"], "--profile", "nl2repo", "--json", prompt,
    ]
    result = run_batch_container(arguments, f"dsh-{run_id}", run_dir,
                                 options["task_timeout_seconds"], env=env)
    result.update(
        task_uuid=run_id, workspace_path=str(workspace), config_path=str(profile),
        image=image, team=summarize_sessions(home / "sessions", options["teammates"]),
        session_logs_path=str(home / "sessions"),
    )
    # Team checks are reported in team["status"]; they must not discard scorable code.
    result["status"] = "completed" if result["generation_status"] == "completed" else "failed"
    if result.get("cleanup_error"):
        result["status"] = "failed"
    return evaluate_result(result, test_data, evaluate)


def start_deepseek(config: dict) -> list:
    """Validate every task and endpoint before starting any containers."""
    pool_size = positive_int(config.get("max_pool_size", 1), "max_pool_size")
    evaluate = evaluation_enabled(config)
    supplied = config.get("deepseek", {})
    if not isinstance(supplied, dict):
        raise ValueError("deepseek must be an object")
    unknown = supplied.keys() - {"image", "teammates", "task_timeout_seconds"}
    if unknown:
        raise ValueError(f"Unknown deepseek settings: {sorted(unknown)}")
    options = {
        "teammates": positive_int(supplied.get("teammates", 2), "deepseek.teammates"),
        "task_timeout_seconds": positive_int(supplied.get("task_timeout_seconds", 7200), "deepseek.task_timeout_seconds"),
    }
    if options["teammates"] < 2:
        raise ValueError("At least two teammates are required for teammate-to-teammate communication")
    specifications = [(task, *resolve_model(entry)) for entry, _, task in model_tasks(config)]
    image = _image_info(supplied.get("image", DEFAULT_IMAGE))
    jobs = []
    for task, model, secret_env in specifications:
        run_id = new_run_id(task.proName, model["model"])
        jobs.append(Job(run_id, "deepseek", model["model"], task.proName,
                        partial(_run_task, run_id, task, model, secret_env, options, image, evaluate)))
    return run_batch(jobs, pool_size)
