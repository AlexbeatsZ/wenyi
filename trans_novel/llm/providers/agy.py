"""通过本机 Antigravity ``agy`` CLI 完成普通非交互提示。"""

from __future__ import annotations

import re
import subprocess
import threading
from pathlib import Path
from typing import Optional

from ...config import LLMConfig, TierConfig
from ..base import LLMClient, Messages
from ..tiers import resolve_tier
from ..usage import UsageSample

_ANSI_RE = re.compile(r"\x1b(?:\][^\x07]*(?:\x07|\x1b\\)|\[[0-?]*[ -/]*[@-~])")
_DEFAULT_TIERS = {
    "strong": TierConfig(model="Gemini 3.1 Pro (High)"),
    "cheap": TierConfig(model="Gemini 3.5 Flash (Medium)"),
    "fast": TierConfig(model="Gemini 3.5 Flash (Low)"),
}
_MODEL_ALIASES = {
    # OpenClaw agy provider 使用的稳定短 ID -> agy 1.0.13 接受的显示名。
    "gemini-3.1-pro": "Gemini 3.1 Pro (Low)",
    "gemini-3.1-pro-low": "Gemini 3.1 Pro (Low)",
    "gemini-3.1-pro-high": "Gemini 3.1 Pro (High)",
    "gemini-3.5-flash": "Gemini 3.5 Flash (Medium)",
    "gemini-3.5-flash-low": "Gemini 3.5 Flash (Low)",
    "gemini-3.5-flash-medium": "Gemini 3.5 Flash (Medium)",
    "gemini-3.5-flash-high": "Gemini 3.5 Flash (High)",
}
_ROLE_LABELS = {
    "system": "System",
    "user": "User",
    "assistant": "Assistant",
    "tool": "Tool result",
}
_JSON_REQUIREMENT = (
    "Output requirement:\n"
    "Return only one valid JSON value matching the requested schema. "
    "Do not use Markdown fences or add explanatory text."
)


def format_agy_prompt(messages: Messages, *, json_mode: bool = False) -> str:
    """把多角色消息折叠为 agy ``--print`` 接受的一条普通提示词。

    agy 1.0.x 没有单次 system prompt 参数，因此 ``System`` 只是明确标注的
    普通提示词前缀，不冒充原生 system 消息。
    """
    sections: list[str] = []
    for message in messages:
        content = str(message.get("content", "")).strip()
        if not content:
            continue
        role = str(message.get("role", "user")).lower()
        label = _ROLE_LABELS.get(role, role.title() or "User")
        sections.append(f"{label}:\n{content}")
    if json_mode:
        sections.append(_JSON_REQUIREMENT)
    return "\n\n".join(sections).strip()


def _estimate_tokens(text: str) -> int:
    """agy 不返回 usage；按字符数给现有统计器提供明确的近似值。"""
    return max(1, round(len(text) / 4)) if text else 0


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text).strip()


class AgyClient(LLMClient):
    """每次以全新 ``agy --print`` 调用执行请求的 CLI provider。"""

    def __init__(self, cfg: LLMConfig) -> None:
        super().__init__()
        self.command = cfg.command or "agy"
        self.cwd = str(Path(cfg.cwd).expanduser()) if cfg.cwd else None
        if self.cwd and not Path(self.cwd).is_dir():
            raise ValueError(f"agy provider 的 cwd 不是现有目录：{self.cwd}")
        self.timeout = max(1, int(cfg.timeout))
        self.tiers = {**_DEFAULT_TIERS, **cfg.tiers}
        # agy 会维护本机项目/会话状态；串行化与 OpenClaw 的适配策略一致，
        # 避免 Wenyi 并发阶段在 Windows 上争用同一状态文件。
        self._process_lock = threading.Lock()

    def complete(
        self,
        messages: Messages,
        *,
        tier: str = "strong",
        json_mode: bool = False,
        max_tokens: Optional[int] = None,
        stage: Optional[str] = None,
    ) -> str:
        """把消息作为普通提示传给独立的 agy print 会话并返回纯文本。"""
        del max_tokens  # agy 1.0.x 的 print 模式没有输出 token 上限参数。
        tier_config = resolve_tier(self.tiers, tier)
        model = tier_config.model
        if not model:
            raise ValueError(f"agy provider 的 {tier} 档未配置 model")
        model = _MODEL_ALIASES.get(model.lower(), model)

        prompt = format_agy_prompt(messages, json_mode=json_mode)
        if not prompt:
            raise ValueError("agy provider 收到空提示词")

        args = [
            self.command,
            "--model",
            model,
            "--print-timeout",
            f"{self.timeout}s",
            "--print",
            prompt,
        ]
        try:
            with self._process_lock:
                result = subprocess.run(
                    args,
                    cwd=self.cwd,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=self.timeout + 5,
                    check=False,
                )
        except FileNotFoundError as exc:
            raise RuntimeError(
                f"找不到 agy CLI：{self.command!r}；请先安装并确认其位于 PATH"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"agy CLI 调用在 {self.timeout} 秒后超时") from exc

        stdout = _strip_ansi(result.stdout or "")
        stderr = _strip_ansi(result.stderr or "")
        if result.returncode != 0:
            detail = stderr or stdout or "无错误输出"
            raise RuntimeError(f"agy CLI 退出码 {result.returncode}：{detail}")

        text = stdout or stderr
        self.usage.record(
            tier,
            UsageSample(
                prompt_tokens=_estimate_tokens(prompt),
                completion_tokens=_estimate_tokens(text),
                total_tokens=_estimate_tokens(prompt) + _estimate_tokens(text),
            ),
            stage=stage,
        )
        return text
