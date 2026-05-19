"""Startup-validated config for an agent service.

Patterns (see ../SKILL.md):
  - Crash at boot on missing or invalid env, not on the first request.
  - Read secrets from FILES, not env values. Env points at the file path.
  - Log a secret FINGERPRINT (first 4 + last 4 chars) so on-call can confirm
    which key is in use without exposing it.
  - Expose the deployment tuple as structured fields for log tagging.

Usage:
    from config import CONFIG, log_startup
    log_startup()
    # CONFIG.anthropic_api_key  -> the resolved value, in-memory only
    # CONFIG.model_id, CONFIG.prompt_hash, ... -> for log enrichment
"""

from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

log = logging.getLogger(__name__)

AppEnv = Literal["dev", "staging", "prod"]


class ConfigError(SystemExit):
    """Fail-fast config errors. Inherits SystemExit so the process actually dies."""


def _require_env(name: str) -> str:
    val = os.environ.get(name, "").strip()
    if not val:
        raise ConfigError(f"Missing required env var: {name}")
    return val


def _require_secret_file(env_name: str) -> str:
    path_str = _require_env(env_name)
    path = Path(path_str)
    if not path.is_file():
        raise ConfigError(f"{env_name} points at {path} which does not exist or is not a file")
    val = path.read_text(encoding="utf-8").strip()
    if not val:
        raise ConfigError(f"Secret file at {path} is empty")
    return val


def _optional_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError:
        raise ConfigError(f"{name} must be a float, got {raw!r}")


def _fingerprint(secret: str) -> str:
    if len(secret) < 12:
        return "****"
    return f"{secret[:4]}…{secret[-4:]}"


@dataclass(frozen=True)
class Config:
    app_env: AppEnv

    # Deployment tuple — used to tag every log line.
    model_id: str
    prompt_hash: str
    tools_hash: str
    code_sha: str
    eval_baseline: str

    # Secrets (held in memory; never log the value).
    anthropic_api_key: str

    # Cost guardrails.
    per_tenant_daily_usd_cap: float
    per_tenant_monthly_usd_cap: float
    per_tenant_rpm_limit: float
    per_run_usd_cap: float

    @property
    def deployment_tag(self) -> dict[str, str]:
        return {
            "app_env": self.app_env,
            "model_id": self.model_id,
            "prompt_hash": self.prompt_hash[:12],
            "tools_hash": self.tools_hash[:12],
            "code_sha": self.code_sha[:12],
            "eval_baseline": self.eval_baseline,
        }


def load() -> Config:
    app_env = _require_env("APP_ENV")
    if app_env not in ("dev", "staging", "prod"):
        raise ConfigError(f"APP_ENV must be dev|staging|prod, got {app_env!r}")

    return Config(
        app_env=app_env,  # type: ignore[arg-type]
        model_id=_require_env("MODEL_ID"),
        prompt_hash=_require_env("PROMPT_HASH"),
        tools_hash=_require_env("TOOLS_HASH"),
        code_sha=_require_env("CODE_SHA"),
        eval_baseline=os.environ.get("EVAL_BASELINE", "unknown"),
        anthropic_api_key=_require_secret_file("ANTHROPIC_API_KEY_FILE"),
        per_tenant_daily_usd_cap=_optional_float("PER_TENANT_DAILY_USD_CAP", 5.0),
        per_tenant_monthly_usd_cap=_optional_float("PER_TENANT_MONTHLY_USD_CAP", 100.0),
        per_tenant_rpm_limit=_optional_float("PER_TENANT_RPM_LIMIT", 20.0),
        per_run_usd_cap=_optional_float("PER_RUN_USD_CAP", 1.0),
    )


try:
    CONFIG = load()
except ConfigError as e:
    print(f"FATAL: {e}", file=sys.stderr)
    raise


def log_startup() -> None:
    """Emit one structured line confirming the deployment tuple and key fingerprint."""
    log.info(
        "startup",
        extra={
            **CONFIG.deployment_tag,
            "anthropic_api_key_fp": _fingerprint(CONFIG.anthropic_api_key),
            "per_tenant_daily_usd_cap": CONFIG.per_tenant_daily_usd_cap,
            "per_run_usd_cap": CONFIG.per_run_usd_cap,
        },
    )
