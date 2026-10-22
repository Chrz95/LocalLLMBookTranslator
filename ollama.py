"""Native Ollama transport, sharing the application's translation/review prompts."""
from __future__ import annotations

from urllib.parse import urlsplit

from lmstudio import APIError, LMStudioClient, LocalModel
from settings_manager import Settings


def cloud_model(identifier: str, details: dict | None = None) -> bool:
    details = details or {}
    return bool(details.get("remote_host") or details.get("remote_model") or
                identifier.casefold().endswith((":cloud", "-cloud")))


def model_rows(payload: dict) -> list[dict]:
    rows = payload.get("models")
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise APIError("Malformed Ollama model list.", retryable=False)
    return rows


def parse_ollama_models(payload: dict, running: dict | None = None) -> list[LocalModel]:
    loaded = {row.get("name") or row.get("model") for row in model_rows(running or {"models": []})}
    models = {}
    for row in model_rows(payload):
        identifier = row.get("name") or row.get("model")
        if not isinstance(identifier, str) or not identifier.strip():
            raise APIError("Ollama model entry has no name.", retryable=False)
        if cloud_model(identifier, row) or row.get("capabilities") == ["embedding"]:
            continue
        models[identifier] = LocalModel(identifier, identifier, "Loaded" if identifier in loaded else "Downloaded")
    return sorted(models.values(), key=lambda model: (model.state != "Loaded", model.identifier.casefold()))


class OllamaClient(LMStudioClient):
    service_name = "Ollama"

    def __init__(self, settings: Settings, endpoint: str | None = None):
        super().__init__(settings, endpoint or settings.ollama_url.strip().rstrip("/") + "/api/chat")

    def complete(self, system: str, user: str) -> str:
        if cloud_model(self.settings.model):
            raise APIError("Select a downloaded local Ollama model instead of a cloud model.", retryable=False)
        result = self._json_request(self.endpoint, {"model": self.settings.model, "stream": False,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]})
        if isinstance(result.get("error"), str):
            raise APIError(f"Ollama: {result['error']}")
        if result.get("done") is not True or result.get("done_reason") not in (None, "stop"):
            raise APIError("Ollama did not finish normally; output was not saved.")
        message = result.get("message")
        if not isinstance(message, dict) or not isinstance(message.get("content"), str):
            raise APIError("Ollama returned a malformed or non-text response.")
        # Thinking is a separate field; only final content may reach the book.
        return message["content"].strip()

    def list_models(self) -> list[LocalModel]:
        address = urlsplit(self.endpoint)
        origin = f"{address.scheme}://{address.netloc}"
        downloaded = self._json_request(origin + "/api/tags", timeout=5)
        try:
            running = self._json_request(origin + "/api/ps", timeout=5)
        except APIError as exc:
            if exc.status_code in (401, 403):
                raise
            running = {"models": []}
        return parse_ollama_models(downloaded, running)
