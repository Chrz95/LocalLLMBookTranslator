"""Validated configuration and crash-safe local file writes."""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

last_input_file = ""


@dataclass(frozen=True)
class Settings:
    model: str = ""
    input_lang: str = "German"
    output_lang: str = "English"
    max_workers: int = 2
    retries: int = 2
    timeout: float = 180.0
    proofread: bool = False
    api_key: str = ""

    def validate(self) -> None:
        for name in ("model", "input_lang", "output_lang"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} is required.")
        if type(self.max_workers) is not int or not 1 <= self.max_workers <= 32:
            raise ValueError("Workers must be an integer from 1 to 32.")
        if type(self.retries) is not int or not 0 <= self.retries <= 10:
            raise ValueError("Retries must be an integer from 0 to 10.")
        if not isinstance(self.timeout, (int, float)) or not 1 <= self.timeout <= 3600:
            raise ValueError("Timeout must be between 1 and 3600 seconds.")
        if type(self.proofread) is not bool or not isinstance(self.api_key, str):
            raise ValueError("Invalid proofreading or API key setting.")


def atomic_write(path: Path, data: str | bytes) -> None:
    """Write on the same filesystem, then atomically replace the destination."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = data.encode("utf-8") if isinstance(data, str) else data
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def save_settings(path: Path, settings: Settings) -> None:
    settings.validate()
    values = asdict(settings)
    values.pop("api_key")  # Credentials stay in memory, never in settings/logs.
    values["last_input_file"] = last_input_file
    atomic_write(path, json.dumps(values, ensure_ascii=False, indent=2))


def load_settings(path: Path) -> Settings:
    global last_input_file
    if not path.exists():
        return Settings()
    values = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(values, dict):
        raise ValueError("Settings must be a JSON object.")
    last_input_file = str(values.pop("last_input_file", ""))
    settings = Settings(**values)
    if settings.model:
        settings.validate()
    return settings
