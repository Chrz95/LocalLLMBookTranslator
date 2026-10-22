"""Provider selection and cache identity, independent of GUI widgets."""
from __future__ import annotations

from lmstudio import LMStudioClient
from ollama import OllamaClient
from settings_manager import Settings


def provider_label(provider: str) -> str:
    return {"lmstudio": "LM Studio", "ollama": "Ollama"}[provider]


def client_identity(settings: Settings) -> dict[str, str]:
    url = settings.ollama_url if settings.provider == "ollama" else settings.lmstudio_url
    return {"provider": settings.provider, "server_url": url.strip().rstrip("/")}


def create_client(settings: Settings, endpoint: str | None = None) -> LMStudioClient:
    settings.validate(require_model=False)
    cls = OllamaClient if settings.provider == "ollama" else LMStudioClient
    return cls(settings, endpoint)
