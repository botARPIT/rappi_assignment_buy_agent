"""Select the live model provider or the explicitly labelled demo test double."""

import os

from langchain_ollama import ChatOllama

from .scripted_model import ScriptedPurchasingModel


def build_model(provider=None):
    provider = provider or os.getenv("AGENT_PROVIDER", "ollama")
    if provider == "scripted":
        return ScriptedPurchasingModel()
    if provider != "ollama":
        raise ValueError("AGENT_PROVIDER must be ollama or scripted")
    return ChatOllama(
        model=os.getenv("AGENT_MODEL", "gpt-oss:20b-cloud"),
        base_url=os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434"),
        temperature=0,
        client_kwargs={"timeout": 90},
    )
