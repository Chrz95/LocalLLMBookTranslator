"""Validated configuration and crash-safe local file writes."""
from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlsplit

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
    custom_prompt: str = ""
    use_custom_prompt: bool = False
    start_lm_studio: bool = False
    lmstudio_path: str = ""
    analyze_novel: bool = False
    analysis_preference_version: int = 2
    provider: str = "lmstudio"
    lmstudio_url: str = "http://localhost:1234"
    ollama_url: str = "http://localhost:11434"

    def validate(self, require_model: bool = True) -> None:
        for name in ("model", "input_lang", "output_lang"):
            value = getattr(self, name)
            if name == "model" and not require_model and value == "":
                continue
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
        if not isinstance(self.custom_prompt, str) or type(self.use_custom_prompt) is not bool:
            raise ValueError("Invalid custom prompt setting.")
        if self.use_custom_prompt and not self.custom_prompt.strip():
            raise ValueError("Enter a custom prompt or disable the custom prompt option.")
        if type(self.start_lm_studio) is not bool or not isinstance(self.lmstudio_path, str):
            raise ValueError("Invalid LM Studio launch preference.")
        if type(self.analyze_novel) is not bool:
            raise ValueError("Novel analysis preference must be a boolean.")
        if self.provider not in ("lmstudio", "ollama"):
            raise ValueError("Choose LM Studio or Ollama as the provider.")
        if type(self.analysis_preference_version) is not int or self.analysis_preference_version != 2:
            raise ValueError("Unsupported analysis preference version.")
        for value in (self.lmstudio_url, self.ollama_url):
            if not isinstance(value, str):
                raise ValueError("Server URL must be text.")
            address = urlsplit(value)
            if (any(char.isspace() for char in value) or address.scheme not in ("http", "https") or not address.hostname
                    or address.username or address.password or address.query or address.fragment
                    or address.path not in ("", "/") or address.port == 0):
                raise ValueError("Use an HTTP(S) server base URL, without credentials or an API path.")


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


def save_settings(path: Path, settings: Settings, require_model: bool = True) -> None:
    settings.validate(require_model=require_model)
    values = asdict(settings)
    values["start_lm_studio"] = False
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
    if "analysis_preference_version" not in values:
        # Previous builds persisted the old default as true; migrate it off once.
        values["analyze_novel"] = False
    values["start_lm_studio"] = False
    settings = Settings(**values)
    settings.validate(require_model=False)
    return settings


@dataclass(frozen=True)
class Prompt:
    identifier: str
    name: str
    text: str


class PromptStore:
    """Persistent named prompts. The built-in default is always available/read-only."""
    DEFAULT_ID = "default"
    DEFAULT_NAME = "Default literary translation"

    def __init__(self, path: Path, default_text: str, read_only: bool = False):
        self.path = Path(path)
        self.default = Prompt(self.DEFAULT_ID, self.DEFAULT_NAME, default_text)
        self.read_only = read_only
        self._prompts: list[Prompt] = []
        self.selected_id = self.DEFAULT_ID
        if self.path.exists() and not read_only:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(data.get("prompts"), list):
                raise ValueError("Invalid prompt library. Repair prompts.json before editing it.")
            for item in data["prompts"]:
                if not isinstance(item, dict) or set(item) != {"identifier", "name", "text"}:
                    raise ValueError("Invalid saved prompt entry.")
                prompt = Prompt(**item)
                self._validate(prompt.name, prompt.text)
                if (not isinstance(prompt.identifier, str) or not prompt.identifier
                    or not re.fullmatch(r"[0-9a-f]{32}", prompt.identifier)
                    or prompt.identifier in {p.identifier for p in self.all()}):
                    raise ValueError("Invalid or duplicate prompt identifier.")
                self._prompts.append(prompt)
            selected = data.get("selected_id", self.DEFAULT_ID)
            self.selected_id = selected if any(p.identifier == selected for p in self.all()) else self.DEFAULT_ID

    def all(self) -> tuple[Prompt, ...]:
        return (self.default, *self._prompts)

    def get(self, identifier: str) -> Prompt:
        for prompt in self.all():
            if prompt.identifier == identifier:
                return prompt
        raise ValueError("Prompt does not exist.")

    def _validate(self, name: str, text: str, exclude: str | None = None) -> None:
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 100:
            raise ValueError("Enter a prompt name from 1 to 100 characters.")
        if not isinstance(text, str) or not text.strip():
            raise ValueError("Prompt text cannot be empty.")
        if name.strip() == "Book prompt (unsaved)":
            raise ValueError("Choose a different prompt name.")
        if any(p.name.casefold() == name.strip().casefold() and p.identifier != exclude for p in self.all()):
            raise ValueError("A prompt with that name already exists.")

    def _commit(self, prompts: list[Prompt], selected_id: str) -> None:
        if self.read_only:
            raise ValueError("Prompt library is read-only because its saved file could not be loaded.")
        atomic_write(self.path, json.dumps({"version": 1, "selected_id": selected_id,
                                           "prompts": [asdict(p) for p in prompts]},
                                          ensure_ascii=False, indent=2))
        self._prompts, self.selected_id = prompts, selected_id

    def create(self, name: str, text: str) -> Prompt:
        import uuid
        self._validate(name, text)
        prompt = Prompt(uuid.uuid4().hex, name.strip(), text)
        self._commit([*self._prompts, prompt], prompt.identifier)
        return prompt

    def update(self, identifier: str, name: str, text: str) -> Prompt:
        self.get(identifier)
        if identifier == self.DEFAULT_ID:
            raise ValueError("The default prompt is read-only. Create a new prompt to customize it.")
        self._validate(name, text, exclude=identifier)
        prompt = Prompt(identifier, name.strip(), text)
        self._commit([prompt if p.identifier == identifier else p for p in self._prompts], self.selected_id)
        return prompt

    def delete(self, identifier: str) -> None:
        self.get(identifier)
        if identifier == self.DEFAULT_ID:
            raise ValueError("The default prompt cannot be deleted.")
        selected = self.DEFAULT_ID if self.selected_id == identifier else self.selected_id
        self._commit([p for p in self._prompts if p.identifier != identifier], selected)

    def select(self, identifier: str) -> Prompt:
        prompt = self.get(identifier)
        self._commit(list(self._prompts), identifier)
        return prompt
