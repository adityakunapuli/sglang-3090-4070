"""Gateway configuration loading and validation.

Loads config.yaml into typed dataclasses at startup. All config is
immutable after initialization — changes require a container restart.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

import yaml


@dataclass(frozen=True)
class ModelOverride:
    """Per-model override parameters applied to incoming requests.

    Attributes:
        model: Upstream model name, or ``"auto"`` to resolve dynamically.
        temperature: Sampling temperature override.
        top_p: Nucleus sampling parameter.
        max_tokens: Maximum completion tokens.
        max_model_len: Maximum context length for this model.
        context_length: Effective context window.
        extra_body: Extra keys hoisted to root of upstream payload
            (e.g. ``chat_template_kwargs``).
    """

    model: str = "auto"
    temperature: float | None = None
    top_p: float | None = None
    frequency_penalty: float | None = None
    presence_penalty: float | None = None
    max_tokens: int | None = None
    max_model_len: int = 0
    context_length: int = 0
    extra_body: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LoggingConfig:
    """Structured log output settings.

    Attributes:
        file: Path to the rotating JSON log file.
        rotation_mb: Rotate after this many megabytes.
        max_files: Number of rotated log files to retain.
        level: Minimum log level (``DEBUG``, ``INFO``, ``WARNING``, ``ERROR``).
    """

    file: str = "./logs/gateway.json"
    rotation_mb: int = 50
    max_files: int = 5
    level: str = "INFO"

    @property
    def rotation_bytes(self) -> int:
        return self.rotation_mb * 1024 * 1024


@dataclass(frozen=True)
class GatewayConfig:
    """Top-level gateway configuration.

    Attributes:
        api_base: Upstream LLM backend URL (e.g. llama-swap or llama-cpp).
        allowed_tokens: List of Bearer tokens permitted for all consumers.
        per_consumer_keys: Map of consumer name to its unique token.
        models: Map of model alias → ``ModelOverride``.
        logging: Structured logging settings.
        rate_limit_rpm: Optional global rate limit (req/min).
    """

    api_base: str
    allowed_tokens: tuple[str, ...] = ()
    per_consumer_keys: dict[str, str] = field(default_factory=dict)
    models: dict[str, ModelOverride] = field(default_factory=dict)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    rate_limit_rpm: int | None = None
    consumer_priorities: dict[str, int] = field(default_factory=dict)
    consumer_cooldown: dict[str, int] = field(default_factory=dict)

    @property
    def has_auth(self) -> bool:
        """True when at least one allowed token is configured."""
        return len(self.allowed_tokens) > 0

    @property
    def has_qos(self) -> bool:
        """True when any QoS constraint (priority or cooldown) is active."""
        return len(self.consumer_priorities) > 0 or len(self.consumer_cooldown) > 0


def _parse_logging(raw: dict[str, Any] | None) -> LoggingConfig:
    """Parse the logging subsection of config.yaml."""
    if not raw:
        return LoggingConfig()
    return LoggingConfig(
        file=raw.get("file", "./logs/gateway.json"),
        rotation_mb=int(raw.get("rotation_mb", 50)),
        max_files=int(raw.get("max_files", 5)),
        level=str(raw.get("level", "INFO")).upper(),
    )


def _parse_models(raw: dict[str, Any] | None) -> dict[str, ModelOverride]:
    """Parse the models subsection of config.yaml."""
    models: dict[str, ModelOverride] = {}
    for name, cfg in (raw or {}).items():
        models[name] = ModelOverride(
            model=str(cfg.get("model", "auto")),
            temperature=cfg.get("temperature"),
            top_p=cfg.get("top_p"),
            frequency_penalty=cfg.get("frequency_penalty"),
            presence_penalty=cfg.get("presence_penalty"),
            max_tokens=cfg.get("max_tokens"),
            max_model_len=int(cfg.get("max_model_len", 0)),
            context_length=int(cfg.get("context_length", 0)),
            extra_body=cfg.get("extra_body", {}),
        )
    return models


def load_config(path: str | None = None) -> GatewayConfig:
    """Load and validate gateway configuration from a YAML file.

    Reads the config file at the given *path* (defaults to the
    ``CONFIG_PATH`` env var, then ``config.yaml`` in the working dir).

    Returns:
        A validated ``GatewayConfig`` instance.

    Raises:
        FileNotFoundError: If the config file does not exist.
        yaml.YAMLError: If the file contains invalid YAML.
    """
    if path is None:
        path = os.getenv("CONFIG_PATH", "config.yaml")

    with open(path) as f:
        raw: dict[str, Any] = yaml.safe_load(f)

    keys_section: dict[str, Any] = raw.get("keys") or {}
    allowed_tokens = tuple(keys_section.get("allowed_tokens") or [])

    qos_section: dict[str, Any] = raw.get("qos") or {}
    consumer_priorities = {
        k: int(v) for k, v in (qos_section.get("priorities") or {}).items()
    }
    consumer_cooldown = {
        k: int(v) for k, v in (qos_section.get("cooldown") or {}).items()
    }

    return GatewayConfig(
        api_base=str(raw.get("api_base", "http://192.168.254.111:8082/v1/chat/completions")),
        allowed_tokens=allowed_tokens,
        per_consumer_keys=keys_section.get("per_consumer") or {},
        models=_parse_models(raw.get("models")),
        logging=_parse_logging(raw.get("logging")),
        rate_limit_rpm=(raw.get("rate_limit") or {}).get("requests_per_minute"),
        consumer_priorities=consumer_priorities,
        consumer_cooldown=consumer_cooldown,
    )
