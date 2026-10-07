"""Translate benchmark model settings into an isolated Cordis profile."""

import os
import re
from pathlib import Path
from urllib.parse import urlparse

from runner import positive_int, write_json

PROFILE_DIR = Path(__file__).parent / "profile"
PROTOCOLS = {"deepseek-messages", "openai-completions", "openai-responses", "anthropic-messages"}


def resolve_model(entry: dict) -> tuple:
    """Return public model settings and a separate secret-bearing environment."""
    protocol = entry.get("protocol", "openai-completions")
    if protocol not in PROTOCOLS:
        raise ValueError(f"protocol must be one of {sorted(PROTOCOLS)}")
    model = entry["moduleName"]
    base_url = entry.get("baseUrl") or os.environ.get("NL2REPO_BASE_URL")
    if not isinstance(base_url, str) or urlparse(base_url).scheme not in {"http", "https"} or not urlparse(base_url).netloc:
        raise ValueError("Set baseUrl or NL2REPO_BASE_URL to the model API base URL")
    key_env = entry.get("apiKeyEnv", "NL2REPO_API_KEY")
    if not isinstance(key_env, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key_env):
        raise ValueError("apiKeyEnv must name an environment variable")
    key = os.environ.get(key_env) or entry.get("sk")
    if not isinstance(key, str) or not key.strip():
        raise ValueError(f"Set {key_env} before launching DeepSeek Harness")
    public = {
        "model": model, "base_url": base_url, "protocol": protocol,
        "context_window": positive_int(entry.get("contextWindow", 131072), "contextWindow"),
        "max_tokens": positive_int(entry.get("maxTokens", 16384), "maxTokens"),
    }
    if public["max_tokens"] > public["context_window"]:
        raise ValueError("maxTokens must not exceed contextWindow")
    effort = entry.get("reasoningEffort")
    if effort is not None:
        if not isinstance(effort, str) or not effort:
            raise ValueError("reasoningEffort must be a nonempty string")
        public["reasoning_effort"] = effort
    for field in ("compat", "reasoningEfforts"):
        if field in entry:
            value = entry[field]
            if not isinstance(value, dict) and not (field == "reasoningEfforts" and value is False):
                raise ValueError(f"{field} must be an object" + (" or false" if field == "reasoningEfforts" else ""))
            public[field] = value
    if protocol == "deepseek-messages":
        if effort is not None and effort not in {"off", "low", "high", "max"}:
            raise ValueError("DeepSeek Messages reasoningEffort must be off, low, high, or max")
        if "compat" in public or "reasoningEfforts" in public:
            raise ValueError("compat and reasoningEfforts apply only to the pi-ai protocols")
    return public, {"NL2REPO_MODEL_API_KEY": key}


def write_profile(home: Path, model: dict, teammates: int) -> Path:
    """Use JSON (valid YAML) so endpoint values cannot become YAML expressions."""
    profile = home / "profiles" / "nl2repo"
    profile.mkdir(parents=True)
    (profile / "package.json").write_bytes((PROFILE_DIR / "package.json").read_bytes())
    native = model["protocol"] == "deepseek-messages"
    provider = "deepseek-official" if native else "nl2repo-endpoint"
    selection = {"provider": provider, "model": model["model"]}
    if "reasoning_effort" in model:
        selection["reasoningEffort"] = model["reasoning_effort"]
    patches = [
        {"id": "agent-default-model", "config": selection},
        {"id": "session-persistence-jsonl", "config": {"root": "/dsh-home/sessions", "compression": "none"}},
        {"id": "agent-team", "config": {"maxMembers": teammates}},
        # Teammates run as subagent continuations and share this pool.
        {"id": "subagent", "config": {"maxActiveSubagents": teammates}},
        {"id": "session-log-deepseek", "config": {"enabled": False}},
    ]
    # Batch runs need no title model, account route, external search, or alternative delegation tools.
    for plugin in ("session-title-llm", "llm-deepseek-account", "tool-web", "web-search-deepseek",
                   "tool-workflow", "plugin-package-inventory-deepseek"):
        patches.append({"id": plugin, "disabled": True})
    catalog_model = {
        "id": model["model"], "contextWindow": model["context_window"], "maxTokens": model["max_tokens"],
    }
    if native:
        patches.append({"id": "llm-deepseek", "config": {
            "baseURL": model["base_url"], "apiKeyEnv": "NL2REPO_MODEL_API_KEY", "models": [catalog_model],
        }})
    else:
        if "reasoningEfforts" in model:
            catalog_model["reasoningEfforts"] = model["reasoningEfforts"]
        route = {
            "api": model["protocol"], "baseURL": model["base_url"],
            "apiKeyEnv": "NL2REPO_MODEL_API_KEY", "models": [catalog_model],
        }
        if "compat" in model:
            route["compat"] = model["compat"]
        patches.extend([
            {"id": "llm-deepseek", "disabled": True},
            {"id": "llm-pi-ai", "config": {"providers": {provider: route}}},
        ])
    path = profile / "cordis.patch.yml"
    write_json(path, patches)
    return path
