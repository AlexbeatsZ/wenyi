"""全书理解预扫 Agent（廉价档）。

翻译开始前通读**源文**，产出：
- 逐章梗概（chapter digest）：每章一段中文梗概，存入 chapter.meta["source_digest"]；
- 全书概览（book synopsis）：把各章梗概 + 前期分析归并成一份全局概览。

二者作为**恒定前缀**注入翻译 prompt（见 prompts.py），让译者翻任意章节前都"对全书有理解"：
把握主线走向、人物弧光、伏笔与谜底，避免早期章节盲译。全局块全程不变，命中前缀缓存近免费复用。
归并对超长书做分组 map-reduce，避免单次 prompt 超长。
"""

from __future__ import annotations

from dataclasses import dataclass

from ..config import Config
from ..llm.base import ContentPolicyError, LLMClient
from . import prompts
from .base import Agent

# 归并时单次喂入的各章梗概字符预算；超过则分组先归并再合并。
_REDUCE_BUDGET = 12000
_DIGEST_FAILURE_PLACEHOLDER = (
    "（本章梗概因内容策略限制生成失败；翻译时依据当前原文与滚动上下文。）"
)
_BOOK_FAILURE_PLACEHOLDER = (
    "（全书概览因内容策略限制生成失败；翻译时依据章节原文与滚动上下文。）"
)


@dataclass(frozen=True)
class DigestResult:
    """章节梗概结果及可持久化的回退诊断。"""

    text: str
    fallback_used: bool = False
    primary_failure: str = ""
    fallback_failure: str = ""
    terminal_failure: bool = False


class Synopsizer(Agent):
    def __init__(
        self,
        client: LLMClient,
        config: Config,
        *,
        content_fallback_client: LLMClient | None = None,
    ) -> None:
        super().__init__(client, config)
        self.content_fallback_client = content_fallback_client
        self.book_synopsis_fallback_count = 0
        self.book_synopsis_primary_failures: list[str] = []
        self.book_synopsis_fallback_failures: list[str] = []

    def digest_chapter(self, source_text: str) -> str:
        """兼容入口：把单章源文压成一段中文梗概。"""
        return self.digest_chapter_result(source_text).text

    def digest_chapter_result(self, source_text: str) -> DigestResult:
        """生成梗概；内容拒绝或空响应时只回退到专用模型。"""
        if not source_text.strip():
            return DigestResult("")
        system = prompts.render("chapter_digest_system", src=self.src, tgt=self.tgt)
        user = prompts.render("chapter_digest_user", src=self.src, tgt=self.tgt,
                              source=source_text[:8000])
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        try:
            text = (self.client.complete(
                messages,
                tier="fast",
                max_tokens=600,
                stage=type(self).__name__,
            ) or "").strip()
            if text:
                return DigestResult(text)
            primary_failure = "empty_response"
        except ContentPolicyError as error:
            primary_failure = f"content_policy: {error}"
        except Exception as error:
            # 暂态传输错误保留空值，下一次断点续跑可重试主模型。
            return DigestResult(
                "",
                primary_failure=f"{type(error).__name__}: {error}",
            )

        if self.content_fallback_client is None:
            return DigestResult(
                _DIGEST_FAILURE_PLACEHOLDER,
                primary_failure=primary_failure,
                terminal_failure=True,
            )
        try:
            fallback = (self.content_fallback_client.complete(
                messages,
                tier="strong",
                max_tokens=600,
                stage="SynopsizerContentFallback",
            ) or "").strip()
            if fallback:
                return DigestResult(
                    fallback,
                    fallback_used=True,
                    primary_failure=primary_failure,
                )
            fallback_failure = "empty_response"
        except Exception as error:
            fallback_failure = f"{type(error).__name__}: {error}"
        return DigestResult(
            _DIGEST_FAILURE_PLACEHOLDER,
            fallback_used=True,
            primary_failure=primary_failure,
            fallback_failure=fallback_failure,
            terminal_failure=True,
        )

    def book_synopsis(self, digests: list[str], analysis_brief: str) -> str:
        """把各章梗概 + 前期分析归并成全书概览。超长则分组 map-reduce。"""
        self.book_synopsis_fallback_count = 0
        self.book_synopsis_primary_failures = []
        self.book_synopsis_fallback_failures = []
        items = [d.strip() for d in digests if d and d.strip()]
        if not items:
            return ""
        while True:
            groups = self._group(items, _REDUCE_BUDGET)
            if len(groups) == 1:
                return self._synth(groups[0], analysis_brief)
            # 多组：每组先归并为一段较粗的概览，再进入下一轮归并
            items = [self._synth(g, analysis_brief) for g in groups]
            items = [s for s in items if s.strip()]
            if not items:
                return ""

    # ── 内部 ────────────────────────────────────────────────────────────────
    @staticmethod
    def _group(items: list[str], budget: int) -> list[list[str]]:
        """按字符预算贪心打包成若干组（每组 joined 长度尽量 ≤ budget）。"""
        groups: list[list[str]] = []
        cur: list[str] = []
        size = 0
        for it in items:
            if cur and size + len(it) > budget:
                groups.append(cur)
                cur, size = [], 0
            cur.append(it)
            size += len(it) + 1
        if cur:
            groups.append(cur)
        return groups

    def _synth(self, digests: list[str], analysis_brief: str) -> str:
        """把一组章节梗概与风格分析归并成更高层概览。"""
        numbered = "\n".join(f"[{i}] {d}" for i, d in enumerate(digests))
        system = prompts.render("book_synopsis_system", src=self.src, tgt=self.tgt)
        user = prompts.render("book_synopsis_user", src=self.src, tgt=self.tgt,
                              analysis=analysis_brief or "（无）", digests=numbered)
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        try:
            text = (self.client.complete(
                messages,
                tier="fast",
                max_tokens=1200,
                stage=type(self).__name__,
            ) or "").strip()
            if text:
                return text
            primary_failure = "empty_response"
        except ContentPolicyError as error:
            primary_failure = f"content_policy: {error}"
        except Exception:
            # 非策略类暂态错误保持旧语义：本次为空，续跑可重试。
            return ""

        self.book_synopsis_primary_failures.append(primary_failure)
        if self.content_fallback_client is None:
            return _BOOK_FAILURE_PLACEHOLDER
        try:
            fallback = (self.content_fallback_client.complete(
                messages,
                tier="strong",
                max_tokens=1200,
                stage="BookSynopsizerContentFallback",
            ) or "").strip()
            if fallback:
                self.book_synopsis_fallback_count += 1
                return fallback
            fallback_failure = "empty_response"
        except Exception as error:
            fallback_failure = f"{type(error).__name__}: {error}"
        self.book_synopsis_fallback_failures.append(fallback_failure)
        return _BOOK_FAILURE_PLACEHOLDER
