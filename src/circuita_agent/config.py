"""Settings from environment variables (and an optional .env file)."""

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

DEFAULT_BASE_URL = "https://api.meta.ai/v1"
DEFAULT_MODEL = "muse-spark-1.3"
DEFAULT_CIRCUITA_URL = "http://localhost:8000"


def load_dotenv(path: Path) -> dict[str, str]:
    """Read KEY=VALUE lines. Missing file means no values. Never touches os.environ."""
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip("'\"")
    return values


@dataclass(frozen=True)
class Settings:
    model_api_key: str | None
    model_base_url: str
    model_name: str
    reasoning_effort: str
    circuita_url: str
    circuita_api_key: str | None

    @classmethod
    def from_env(
        cls, env: Mapping[str, str] | None = None, dotenv: Path | None = None
    ) -> "Settings":
        """Real environment variables win over the .env file."""
        merged: dict[str, str] = dict(load_dotenv(dotenv or Path(".env")))
        merged.update(os.environ if env is None else env)
        return cls(
            model_api_key=merged.get("MODEL_API_KEY") or None,
            model_base_url=merged.get("MODEL_BASE_URL") or DEFAULT_BASE_URL,
            model_name=merged.get("MODEL_NAME") or DEFAULT_MODEL,
            reasoning_effort=merged.get("MODEL_REASONING_EFFORT") or "low",
            circuita_url=(merged.get("CIRCUITA_URL") or DEFAULT_CIRCUITA_URL).rstrip("/"),
            circuita_api_key=merged.get("CIRCUITA_API_KEY") or None,
        )
