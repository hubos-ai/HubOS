"""Runtime configuration bridge for Console V2 settings."""

from __future__ import annotations

import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from hubos.core.infra.agent_tool_guard import AgentToolGuard


@dataclass
class RuntimeConfigSummary:
    """Summary of runtime config application."""

    updated_agents: int = 0
    model_overrides: int = 0
    tool_pruned: int = 0
    subagent_caps: int = 0

    def to_dict(self) -> dict[str, int]:
        return {
            "updated_agents": self.updated_agents,
            "model_overrides": self.model_overrides,
            "tool_pruned": self.tool_pruned,
            "subagent_caps": self.subagent_caps,
        }


def _enabled_models(settings: dict[str, Any]) -> list[dict[str, Any]]:
    models = settings.get("models", [])
    return [m for m in models if m.get("enabled")]


def _enabled_tools(settings: dict[str, Any]) -> set[str]:
    tools = settings.get("tools", [])
    return {t.get("id", "") for t in tools if t.get("enabled") and t.get("id")}


def _default_parallel_limit(settings: dict[str, Any]) -> int | None:
    workflows = settings.get("workflows", [])
    default_wf = next((w for w in workflows if w.get("enabled") and w.get("default")), None)
    if not default_wf:
        return None
    value = default_wf.get("max_parallel_subagents")
    if isinstance(value, int) and value > 0:
        return value
    return None


def build_effective_runtime_config(registry: Any, settings: dict[str, Any]) -> dict[str, Any]:
    """Build current effective runtime config snapshot."""
    enabled_models = _enabled_models(settings)
    enabled_tools = sorted(list(_enabled_tools(settings)))
    agents = registry.list_agents()
    default_parallel = _default_parallel_limit(settings)

    default_model = enabled_models[0] if enabled_models else None
    return {
        "models": {
            "enabled_count": len(enabled_models),
            "default_model": default_model,
        },
        "tools": {
            "enabled_count": len(enabled_tools),
            "enabled_tools": enabled_tools,
        },
        "workflows": {
            "default_parallel_limit": default_parallel,
        },
        "agents": {
            "count": len(agents),
        },
    }


def apply_settings_to_registry(registry: Any, settings: dict[str, Any]) -> RuntimeConfigSummary:
    """
    Apply settings to currently registered agents.

    - If an agent model is not in enabled model list, fallback to first enabled model.
    - Remove tools from agents if those tools are globally disabled.
    - Cap max_subagents by default workflow parallel limit.
    """
    summary = RuntimeConfigSummary()
    enabled_models = _enabled_models(settings)
    enabled_model_pairs = {
        (m.get("provider", ""), m.get("model", "")) for m in enabled_models if m.get("provider") and m.get("model")
    }
    default_model = enabled_models[0] if enabled_models else None
    enabled_tools = _enabled_tools(settings)
    parallel_cap = _default_parallel_limit(settings)

    for agent in registry.list_agents():
        changed = False
        updates: dict[str, Any] = {}

        provider = agent.model_provider.value if hasattr(agent.model_provider, "value") else str(agent.model_provider)
        model_name = agent.model_name
        if default_model and (provider, model_name) not in enabled_model_pairs:
            from hubos.core.infra.agent_registry import ModelProvider

            target_provider = str(default_model["provider"]).lower()
            try:
                updates["model_provider"] = ModelProvider(target_provider)
            except ValueError:
                # Keep runtime compatible with providers beyond current enum.
                updates["model_provider"] = ModelProvider.CUSTOM
            updates["model_name"] = default_model["model"]
            summary.model_overrides += 1
            changed = True

        if enabled_tools:
            pruned_tools = [t for t in agent.allowed_tools if t in enabled_tools]
            if pruned_tools != agent.allowed_tools:
                summary.tool_pruned += len([t for t in agent.allowed_tools if t not in enabled_tools])
                updates["allowed_tools"] = pruned_tools
                changed = True

        if parallel_cap is not None and agent.max_subagents > parallel_cap:
            updates["max_subagents"] = parallel_cap
            summary.subagent_caps += 1
            changed = True

        if changed:
            registry.update_agent(agent.agent_id, **updates)
            summary.updated_agents += 1

    return summary


def build_permissions_matrix(registry: Any, settings: dict[str, Any]) -> dict[str, Any]:
    """Build per-agent tool permissions matrix."""
    tool_policies = settings.get("tools", [])
    configured_tools = [t.get("id") for t in tool_policies if t.get("id")]
    if not configured_tools:
        configured_tools = sorted({t for a in registry.list_agents() for t in a.allowed_tools})

    guard = AgentToolGuard(agent_registry=registry, tool_policies=tool_policies)
    rows: list[dict[str, Any]] = []

    for agent in registry.list_agents():
        row = {
            "agent_id": agent.agent_id,
            "agent_name": agent.name,
            "role": agent.role,
            "status": agent.status.value if hasattr(agent.status, "value") else str(agent.status),
            "permissions": {},
        }
        for tool_name in configured_tools:
            result = guard.check_permission(agent.agent_id, tool_name)
            row["permissions"][tool_name] = {
                "allowed": result.allowed,
                "error_code": result.error_code.value,
                "risk_level": result.risk_level,
                "requires_approval": result.requires_approval,
            }
        rows.append(row)

    return {
        "tools": configured_tools,
        "rows": rows,
        "generated_at": time.time(),
    }


def check_model_connection(model_cfg: dict[str, Any]) -> dict[str, Any]:
    """Run a lightweight model provider connectivity test."""
    started = time.time()
    provider = model_cfg.get("provider", "")
    api_key_env = model_cfg.get("api_key_env", "")
    api_key = os.environ.get(api_key_env) if api_key_env else None

    checks: list[dict[str, Any]] = []
    checks.append({"name": "enabled", "ok": bool(model_cfg.get("enabled"))})
    checks.append({"name": "api_key_present", "ok": bool(api_key)})

    if not model_cfg.get("enabled"):
        return _test_result(False, provider, checks, "Model is disabled", started)
    if not api_key:
        return _test_result(False, provider, checks, f"Missing env key: {api_key_env}", started)

    base_url = (model_cfg.get("base_url") or "").rstrip("/")
    headers: dict[str, str] = {}
    url = ""
    if provider == "openai":
        url = (base_url or "https://api.openai.com/v1") + "/models"
        headers = {"Authorization": f"Bearer {api_key}"}
    elif provider == "anthropic":
        url = (base_url or "https://api.anthropic.com/v1") + "/models"
        headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01"}
    elif provider == "minimax":
        url = (base_url or "https://api.minimax.chat/v1") + "/models"
        headers = {"Authorization": f"Bearer {api_key}"}
    else:
        checks.append({"name": "http_probe", "ok": True, "message": "Skipped for this provider"})
        return _test_result(True, provider, checks, "Config looks valid", started)

    req = urllib.request.Request(url=url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=float(model_cfg.get("timeout_seconds", 5))) as response:
            ok = 200 <= response.getcode() < 300
            checks.append({"name": "http_probe", "ok": ok, "status_code": response.getcode(), "url": url})
            return _test_result(ok, provider, checks, f"HTTP {response.getcode()}", started)
    except urllib.error.HTTPError as exc:
        # MiniMax does not always expose a GET /models endpoint.
        # For connectivity checks, treat common client errors as reachable.
        if provider == "minimax" and exc.code in {401, 403, 404, 405}:
            checks.append({"name": "http_probe", "ok": True, "status_code": exc.code, "url": url})
            return _test_result(True, provider, checks, f"Endpoint reachable (HTTP {exc.code})", started)
        checks.append({"name": "http_probe", "ok": False, "status_code": exc.code, "url": url})
        return _test_result(False, provider, checks, f"HTTP error: {exc.code}", started)
    except Exception as exc:  # noqa: BLE001
        checks.append({"name": "http_probe", "ok": False, "error": str(exc), "url": url})
        return _test_result(False, provider, checks, str(exc), started)


def check_channel_connection(channel_cfg: dict[str, Any]) -> dict[str, Any]:
    """Run channel connectivity/configuration test."""
    started = time.time()
    endpoint = channel_cfg.get("endpoint", "")
    checks: list[dict[str, Any]] = []
    checks.append({"name": "enabled", "ok": bool(channel_cfg.get("enabled"))})
    checks.append({"name": "endpoint_present", "ok": bool(endpoint)})

    if not channel_cfg.get("enabled"):
        return _test_result(False, channel_cfg.get("type", "unknown"), checks, "Channel is disabled", started)
    if not endpoint:
        return _test_result(False, channel_cfg.get("type", "unknown"), checks, "Missing endpoint", started)

    if endpoint.startswith("/"):
        checks.append({"name": "endpoint_kind", "ok": True, "kind": "local_path"})
        return _test_result(True, channel_cfg.get("type", "unknown"), checks, "Local endpoint configured", started)

    if endpoint.startswith("http://") or endpoint.startswith("https://"):
        req = urllib.request.Request(url=endpoint, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=3.0) as response:
                code = response.getcode()
                ok = code < 500
                checks.append({"name": "http_probe", "ok": ok, "status_code": code})
                return _test_result(ok, channel_cfg.get("type", "unknown"), checks, f"HTTP {code}", started)
        except urllib.error.HTTPError as exc:
            ok = exc.code < 500
            checks.append({"name": "http_probe", "ok": ok, "status_code": exc.code})
            return _test_result(ok, channel_cfg.get("type", "unknown"), checks, f"HTTP {exc.code}", started)
        except Exception as exc:  # noqa: BLE001
            checks.append({"name": "http_probe", "ok": False, "error": str(exc)})
            return _test_result(False, channel_cfg.get("type", "unknown"), checks, str(exc), started)

    checks.append({"name": "endpoint_format", "ok": False, "message": "Endpoint must be '/' path or http(s) URL"})
    return _test_result(False, channel_cfg.get("type", "unknown"), checks, "Invalid endpoint format", started)


def _test_result(ok: bool, target: str, checks: list[dict[str, Any]], message: str, started: float) -> dict[str, Any]:
    return {
        "ok": ok,
        "target": target,
        "message": message,
        "latency_ms": int((time.time() - started) * 1000),
        "checks": checks,
    }
