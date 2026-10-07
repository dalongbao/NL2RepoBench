"""Run local batch containers with bounded execution and retained diagnostics."""

import json
import subprocess
import time
from pathlib import Path


def docker_command(args: list, **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], check=True, text=True, **kwargs)


def run_batch_container(create_args: list, name: str, directory: Path,
                        timeout_seconds: int, env: dict = None) -> dict:
    """Capture the container's exit status before cleanup; never infer success."""
    stdout_path = directory / "stdout.jsonl"
    stderr_path = directory / "stderr.log"
    lifecycle_path = directory / "container.log"
    result = {
        "container_name": name, "exit_code": None, "generation_status": "error",
        "stdout_path": str(stdout_path), "stderr_path": str(stderr_path),
        "container_log_path": str(lifecycle_path),
    }
    started = time.monotonic()
    # The unique name also permits cleanup if creation succeeds but its CLI fails.
    with lifecycle_path.open("w", encoding="utf-8") as lifecycle:
        try:
            created = docker_command(
                ["create", "--name", name, *create_args], env=env,
                stdout=subprocess.PIPE, stderr=lifecycle, timeout=120,
            )
            container_id = created.stdout.strip()
            result["container_id"] = container_id
            with stdout_path.open("w", encoding="utf-8") as out, stderr_path.open("w", encoding="utf-8") as err:
                try:
                    attached = subprocess.run(
                        ["docker", "start", "--attach", container_id],
                        stdout=out, stderr=err, timeout=timeout_seconds,
                    )
                    result["attach_exit_code"] = attached.returncode
                except subprocess.TimeoutExpired:
                    result["generation_status"] = "timeout"
                    result["error"] = f"Generation exceeded {timeout_seconds} seconds"
                    docker_command(["stop", "--time", "10", container_id],
                                   stdout=lifecycle, stderr=lifecycle, timeout=25)

            inspected = docker_command(["inspect", container_id], stdout=subprocess.PIPE,
                                       stderr=lifecycle, timeout=30)
            container = json.loads(inspected.stdout)[0]
            state = container["State"]
            result["image_id"] = container["Image"]
            result["container_state"] = state
            if not state["Running"]:
                result["exit_code"] = state["ExitCode"]
            if result["generation_status"] != "timeout":
                completed = (state["Status"] == "exited" and state["ExitCode"] == 0
                             and not state.get("OOMKilled") and not state.get("Error")
                             and result.get("attach_exit_code") == 0)
                result["generation_status"] = "completed" if completed else "failed"
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            if result["generation_status"] != "timeout":
                result["generation_status"] = "error"
            # Do not stringify CalledProcessError: its argv may contain endpoint details.
            result["error"] = f"Container operation failed ({type(exc).__name__}); see container.log"
        finally:
            try:
                removed = subprocess.run(["docker", "rm", "--force", name],
                                         stdout=lifecycle, stderr=lifecycle, timeout=30)
                if removed.returncode:
                    result["cleanup_error"] = "Container removal failed; see container.log"
            except (OSError, subprocess.SubprocessError) as exc:
                result["cleanup_error"] = f"Container removal failed ({type(exc).__name__})"
            result["duration_seconds"] = round(time.monotonic() - started, 3)
    return result
