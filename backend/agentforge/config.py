"""Application configuration."""

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings loaded from environment and config file."""

    model_config = SettingsConfigDict(
        env_prefix="AGENTFORGE_",
        env_file=".env",
        extra="ignore",
    )

    app_name: str = "AgentForge"
    host: str = "127.0.0.1"
    port: int = 8765
    data_dir: Path = Field(
        default_factory=lambda: Path.home() / ".local" / "share" / "agentforge"
    )
    workspace_root: Path = Path("/home/joruf/Dokumente")
    default_memory_tokens: int = 32000
    default_model: str = "ollama/llama3.1:8b"
    override_model: str = ""
    ollama_base_url: str = "http://localhost:11434"
    llm_auto_routing: bool = True
    ui_language: str = "en"
    openai_api_key: str = ""
    openai_api_base: str = ""
    anthropic_api_key: str = ""
    gemini_api_key: str = ""
    groq_api_key: str = ""
    mistral_api_key: str = ""
    command_whitelist: list[str] = Field(
        default_factory=lambda: [
            "ls", "cat", "head", "tail", "grep", "find", "pwd", "echo",
            "git", "npm", "node", "python", "python3", "pip", "pip3",
            "php", "composer", "mkdir", "touch", "cp", "mv", "tree",
            "wc", "sort", "uniq", "diff", "file", "which", "date",
        ]
    )
    command_blacklist: list[str] = Field(
        default_factory=lambda: [
            "rm", "rmdir", "sudo", "su", "chmod", "chown", "kill",
            "killall", "shutdown", "reboot", "dd", "mkfs", "fdisk",
            "curl", "wget", "ssh", "scp", "nc", "nmap",
        ]
    )
    max_output_chars: int = 8000
    max_search_results: int = 100
    max_search_file_bytes: int = 1_048_576
    history_preview_chars: int = 300
    llm_request_timeout: float = 300.0
    llm_title_timeout: float = 45.0
    multi_agent_max_rounds_ollama: int = 2
    multi_agent_max_rounds: int = 4
    web_search_enabled: bool = True
    web_search_max_results: int = 5
    web_search_timeout: float = 20.0
    web_search_providers: list[str] = Field(
        default_factory=lambda: ["duckduckgo", "wikipedia", "duckduckgo_instant"]
    )

    @property
    def db_path(self) -> Path:
        """Return the SQLite database path."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        return self.data_dir / "agentforge.db"

    @property
    def roles_dir(self) -> Path:
        """Return the directory containing role definitions."""
        return Path(__file__).resolve().parents[2] / "assets" / "roles"


settings = Settings()
