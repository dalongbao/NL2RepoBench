"""OpenHands headless backend using the shared batch scheduler."""

import json
import os
import shutil
from functools import partial
from pathlib import Path

from docker_self.batch_container import run_batch_container
from runner import Job, evaluate_result, evaluation_enabled, new_run_id, positive_int, run_batch
from test_data_service import test_data_list

APP_IMAGE = "docker.all-hands.dev/all-hands-ai/openhands:0.56"
RUNTIME_IMAGE = "docker.all-hands.dev/all-hands-ai/runtime:0.56-nikolaik"
PROMPT = (
    "According to the start.md in the workspace, implement the entire project as per the "
    "requirements specified in the document, ensuring that the final product can be directly "
    "run in the current directory. The running requirements should comply with the <API Usage "
    "Guide> section of the document. Please complete this task step by step."
)


def _run_task(run_id: str, entry: dict, test_data, timeout: int, evaluate: bool) -> dict:
    run_dir = Path("workspaces").resolve() / run_id
    workspace = run_dir / "workspace"
    workspace.mkdir(parents=True)
    shutil.copyfile(test_data.md, workspace / "start.md")
    state = run_dir / "openhands-state"
    state.mkdir()
    key = os.environ.get(entry.get("apiKeyEnv", "")) or entry.get("sk", "")
    model_config = (
        "[llm.benchmark]\n"
        f"model = {json.dumps(entry['moduleName'])}\n"
        f"api_key = {json.dumps(key)}\n"
    )
    base_url = entry.get("baseUrl") or entry.get("base_url")
    if base_url:
        model_config += f"base_url = {json.dumps(base_url)}\n"
    template = Path("template/config.template.toml").read_text(encoding="utf-8")
    template = template.replace("{{VOLUMES}}", f"volumes = {json.dumps(str(workspace) + ':/workspace:rw')}")
    template = template.replace("{{MODULE_CONFIG}}", model_config)
    template = template.replace("llm_config = 'claude_opus_4_1_thinking'", "llm_config = 'benchmark'")
    config_path = run_dir / "config.toml"
    with config_path.open("x", encoding="utf-8") as stream:
        os.chmod(config_path, 0o600)
        stream.write(template)

    arguments = [
        "--pull", "always",
        "--env", f"SANDBOX_RUNTIME_CONTAINER_IMAGE={RUNTIME_IMAGE}",
        "--env", "LOG_ALL_EVENTS=true",
        "--env", "CONFIG_FILE=/custom/path/config.toml",
        "--env", "AGENT_LLM_CONFIG=benchmark",
        "--volume", f"{config_path}:/custom/path/config.toml:ro",
        "--volume", "/var/run/docker.sock:/var/run/docker.sock:rw",
        "--volume", f"{state}:/.openhands:rw",
        "--add-host", "host.docker.internal:host-gateway",
        APP_IMAGE, "python", "-m", "openhands.core.main",
        "--config-file=/custom/path/config.toml", "-t", PROMPT,
    ]
    result = run_batch_container(arguments, f"openhands-{run_id}", run_dir, timeout)
    result.update(
        task_uuid=run_id, workspace_path=str(workspace), config_path=str(config_path),
        test_data_info={"pro_name": test_data.proName, "test_case_count": test_data.testCaseCount},
        status="completed" if result["generation_status"] == "completed" else "failed",
    )
    if result.get("cleanup_error"):
        result["status"] = "failed"
    return evaluate_result(result, test_data, evaluate)


def start_openhands(config: dict) -> list:
    """Keep the legacy model/task configuration and return persisted batch results."""
    pool_size = positive_int(config.get("max_pool_size", 10), "max_pool_size")
    timeout = positive_int(config.get("task_timeout_seconds", 7200), "task_timeout_seconds")
    evaluate = evaluation_enabled(config)
    tasks = {task.proName: task for task in test_data_list}
    entries = config.get("startPro")
    if not isinstance(entries, list) or not entries:
        raise ValueError("startPro must contain at least one model configuration")
    jobs = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("Each startPro entry must be an object")
        model = entry.get("moduleName")
        if not isinstance(model, str) or not model.strip():
            raise ValueError("moduleName must be a nonempty model ID")
        names = entry.get("proNameList")
        if not isinstance(names, list) or not names:
            raise ValueError("proNameList must contain at least one task name")
        for name in names:
            if not isinstance(name, str) or name not in tasks:
                raise ValueError(f"Unknown benchmark task: {name!r}")
            if not tasks[name].md or not Path(tasks[name].md).is_file():
                raise ValueError(f"Missing task instructions for {name}")
            run_id = new_run_id(name, model)
            jobs.append(Job(run_id, "openhands", model, name,
                            partial(_run_task, run_id, entry, tasks[name], timeout, evaluate)))
    return run_batch(jobs, pool_size)
