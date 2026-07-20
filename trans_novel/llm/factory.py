"""根据配置创建内置 LLM provider。"""

from __future__ import annotations

from ..config import Config, LLMConfig
from .base import LLMClient


def build_client(config: Config) -> LLMClient:
    """根据 llm.provider 延迟导入并构造对应客户端。"""
    return build_client_from_llm(config.llm)


def build_client_from_llm(llm: LLMConfig) -> LLMClient:
    """根据一份主配置或阶段专用配置创建 provider。"""
    provider = llm.provider.strip().lower().replace("_", "-")
    if provider == "deepseek":
        from .providers.deepseek import DeepSeekClient

        return DeepSeekClient(llm)
    if provider == "openai":
        from .providers.openai import OpenAIClient

        return OpenAIClient(llm)
    if provider == "openrouter":
        from .providers.openrouter import OpenRouterClient

        return OpenRouterClient(llm)
    if provider == "openai-compatible":
        from .providers.openai_compatible import OpenAICompatibleClient

        return OpenAICompatibleClient(llm)
    if provider == "ollama":
        from .providers.ollama import OllamaClient

        return OllamaClient(llm)
    if provider == "vllm":
        from .providers.vllm import VLLMClient

        return VLLMClient(llm)
    if provider in {"agy", "agy-cli"}:
        from .providers.agy import AgyClient

        return AgyClient(llm)
    if provider in {"codex", "codex-cli"}:
        from .providers.codex_cli import CodexCLIClient

        return CodexCLIClient(llm)
    if provider == "fake":
        from .providers.fake import FakeClient

        return FakeClient()
    raise ValueError(
        f"未知 provider：{provider}"
        "（支持 deepseek / openai / openrouter / openai-compatible / "
        "ollama / vllm / agy / codex-cli / fake）"
    )
