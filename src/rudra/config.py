"""Configuration settings for Rudra."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv


@dataclass
class OllamaConfig:
    """Ollama LLM configuration."""

    base_url: str = field(
        default_factory=lambda: os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    )
    model: str = field(default_factory=lambda: os.getenv("OLLAMA_MODEL", "qwen3:14b"))
    model_planner: str = field(
        default_factory=lambda: (
            os.getenv("OLLAMA_MODEL_PLANNER") or os.getenv("OLLAMA_MODEL", "qwen3:14b")
        )
    )
    model_coder: str = field(
        default_factory=lambda: (
            os.getenv("OLLAMA_MODEL_CODER") or os.getenv("OLLAMA_MODEL", "qwen3:14b")
        )
    )
    temperature: float = field(
        default_factory=lambda: float(os.getenv("OLLAMA_TEMPERATURE", "0.3"))
    )
    timeout: int = field(default_factory=lambda: int(os.getenv("OLLAMA_TIMEOUT", "300")))
    num_predict: int = field(default_factory=lambda: int(os.getenv("OLLAMA_NUM_PREDICT", "131072")))


@dataclass
class AgentConfig:
    """Agent execution configuration."""

    # Verbose logging — ON by default, set VERBOSE=false in .env to disable
    verbose: bool = field(default_factory=lambda: os.getenv("VERBOSE", "true").lower() != "false")


@dataclass
class Config:
    """Main configuration container."""

    ollama: OllamaConfig = field(default_factory=OllamaConfig)
    agent: AgentConfig = field(default_factory=AgentConfig)

    # Paths
    checkpoint_dir: str = ".rudra"

    @classmethod
    def load(cls) -> "Config":
        """Load configuration from the environment, reading the project's .env first.

        The .env path is explicit rather than bare `load_dotenv()`, which resolves
        by walking upward from *this module's* directory — so a .env anywhere above
        the installed package leaked into every project the user ran Rudra in,
        regardless of the invocation directory. See TODO.md A5.1.

        `override=False` is python-dotenv's default and is deliberately kept: a
        real environment variable still beats .env.
        """
        load_dotenv(Path.cwd() / ".env")
        return cls()

    def get_checkpoint_path(self, project_dir: Path) -> Path:
        """Get the checkpoint directory path for a project."""
        checkpoint_path = project_dir / self.checkpoint_dir
        checkpoint_path.mkdir(parents=True, exist_ok=True)
        return checkpoint_path


_config: Config | None = None


def get_config() -> Config:
    """Return the process-wide Config, loading it on first use.

    Replaces the module-level `config = Config.load()`, which froze the entire
    environment at first import of this module. See TODO.md A1.15.
    """
    global _config
    if _config is None:
        _config = Config.load()
    return _config


def reset_config() -> None:
    """Drop the cached Config so the next get_config() re-reads the environment.

    Test-support only.
    """
    global _config
    _config = None
