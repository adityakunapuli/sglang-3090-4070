"""Tests for config loading and validation."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from app.config import load_config, GatewayConfig, ModelOverride, LoggingConfig


def test_load_config_minimal(tmp_path: Path) -> None:
    """Minimal valid config with just api_base."""
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("api_base: http://localhost:8082/v1/chat/completions\n")
    config = load_config(str(cfg_file))
    assert config.api_base == "http://localhost:8082/v1/chat/completions"
    assert config.allowed_tokens == ()
    assert config.models == {}
    assert isinstance(config.logging, LoggingConfig)


def test_load_config_full(tmp_path: Path) -> None:
    """Full config with keys, models, and logging sections."""
    raw = {
        "api_base": "http://backend:8080/v1/chat/completions",
        "keys": {"allowed_tokens": ["sk-1", "sk-2"], "per_consumer": {"frigate": "sk-frigate"}},
        "models": {
            "frigate": {
                "model": "auto",
                "temperature": 1.1,
                "max_tokens": 512,
                "max_model_len": 8192,
                "context_length": 8192,
                "extra_body": {"chat_template_kwargs": {"enable_thinking": False}},
            }
        },
        "logging": {"file": "/var/log/gateway.json", "rotation_mb": 100, "max_files": 10, "level": "DEBUG"},
    }
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(yaml.dump(raw))
    config = load_config(str(cfg_file))
    assert config.api_base == "http://backend:8080/v1/chat/completions"
    assert config.allowed_tokens == ("sk-1", "sk-2")
    assert config.per_consumer_keys == {"frigate": "sk-frigate"}
    assert config.has_auth is True
    assert "frigate" in config.models
    assert config.models["frigate"].max_model_len == 8192
    assert config.logging.file == "/var/log/gateway.json"
    assert config.logging.rotation_mb == 100
    assert config.logging.level == "DEBUG"


def test_load_config_no_auth(tmp_path: Path) -> None:
    """Config without keys section is permissive."""
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("api_base: http://backend:8080/v1\n")
    config = load_config(str(cfg_file))
    assert config.has_auth is False
    assert config.allowed_tokens == ()


def test_load_config_models_with_overrides(tmp_path: Path) -> None:
    """Model override parsing handles all fields correctly."""
    raw = {
        "models": {
            "test-model": {
                "model": "specific-model",
                "temperature": 0.5,
                "max_tokens": 100,
                "max_model_len": 4096,
                "context_length": 4096,
                "extra_body": {"key": "val"},
            }
        }
    }
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(yaml.dump(raw))
    config = load_config(str(cfg_file))
    m = config.models["test-model"]
    assert m.model == "specific-model"
    assert m.temperature == 0.5
    assert m.max_tokens == 100
    assert m.max_model_len == 4096
    assert m.context_length == 4096
    assert m.extra_body == {"key": "val"}


def test_load_config_missing_file() -> None:
    """Loading a non-existent file raises FileNotFoundError."""
    with pytest.raises(FileNotFoundError):
        load_config("/nonexistent/config.yaml")
