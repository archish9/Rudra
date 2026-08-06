"""Configuration settings for Rudra."""

import os
from pathlib import Path
from dataclasses import dataclass, field
from dotenv import load_dotenv

# Load environment variables
load_dotenv()


@dataclass
class OllamaConfig:
    """Ollama LLM configuration."""
    
    base_url: str = field(default_factory=lambda: os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"))
    model: str = field(default_factory=lambda: os.getenv("OLLAMA_MODEL", "qwen3:14b"))
    model_planner: str = field(default_factory=lambda: os.getenv("OLLAMA_MODEL_PLANNER") or os.getenv("OLLAMA_MODEL", "qwen3:14b"))
    model_coder: str = field(default_factory=lambda: os.getenv("OLLAMA_MODEL_CODER") or os.getenv("OLLAMA_MODEL", "qwen3:14b"))
    temperature: float = field(default_factory=lambda: float(os.getenv("OLLAMA_TEMPERATURE", "0.3")))
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
        """Load configuration from environment."""
        return cls()
    
    def get_checkpoint_path(self, project_dir: Path) -> Path:
        """Get the checkpoint directory path for a project."""
        checkpoint_path = project_dir / self.checkpoint_dir
        checkpoint_path.mkdir(parents=True, exist_ok=True)
        return checkpoint_path


# Global config instance
config = Config.load()
