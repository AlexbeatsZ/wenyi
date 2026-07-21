"""润色 Agent（强档）。

在审校通过的直译稿上做中文文学性二次加工：不增删信息、保持段数不变。
对齐失败（段数不符）时保守地返回原译文，绝不因润色而引入漏译。
"""

from __future__ import annotations

from ..config import Config
from ..glossary.store import GlossaryTerm
from ..llm.base import ContentPolicyError, LLMClient
from . import prompts
from .base import Agent


class PolishResponseError(ValueError):
    """精修回复可解析但不满足严格的一段对一段协议。"""


class Polisher(Agent):
    def __init__(
        self,
        client: LLMClient,
        config: Config,
        *,
        fallback_client: LLMClient | None = None,
        recovery_fallback_client: LLMClient | None = None,
    ) -> None:
        super().__init__(client, config)
        # 原有 fallback 只处理明确的内容策略拒绝，通常是初译 Flash。
        self.fallback_client = fallback_client
        # 独立恢复模型处理坏 JSON、漏项、CLI 暂态失败和策略备用也失败的叶子。
        self.recovery_fallback_client = recovery_fallback_client
        self.last_policy_fallback_indexes: list[int] = []
        self.last_policy_context_fallback_indexes: list[int] = []
        self.last_recovery_fallback_indexes: list[int] = []
        self.last_failed_indexes: list[int] = []
        self.last_failure_details: list[dict[str, object]] = []

    def _call(
        self,
        client: LLMClient,
        targets: list[str],
        *,
        sources: list[str],
        glossary_terms: list[GlossaryTerm],
        style: str,
        context: str,
        book_synopsis: str,
        chapter_digest: str,
        narrative_facts: str,
        stage: str,
    ) -> list[str]:
        n = len(targets)
        system = prompts.render(
            "polisher_system", src=self.src, tgt=self.tgt, n=n,
            quote_style=self.config.punctuation_quote_style,
        )
        user = prompts.render(
            "polisher_user", src=self.src, tgt=self.tgt,
            glossary=prompts.render_glossary(glossary_terms),
            style=style or "（无）",
            narrative_facts=narrative_facts or "（暂无）",
            book_synopsis=book_synopsis or "（无）",
            chapter_digest=chapter_digest or "（无）",
            context=context or "（无）",
            n=n,
            pairs=prompts.numbered_pairs(sources, targets),
        )
        data = client.complete_json(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            tier="strong",
            stage=stage,
        )
        items = data.get("polished") if isinstance(data, dict) else data
        if not isinstance(items, list):
            raise PolishResponseError("精修回复缺少 polished 数组")
        if len(items) != n:
            raise PolishResponseError(
                f"精修回复段数不符：期望 {n}，实际 {len(items)}"
            )
        polished = [str(item).strip() for item in items]
        empty = [index for index, item in enumerate(polished) if not item]
        if empty:
            raise PolishResponseError(
                f"精修回复包含空段：{','.join(str(index) for index in empty[:10])}"
            )
        return polished

    @staticmethod
    def _extend_unique(target: list[int], indexes: list[int]) -> None:
        target[:] = sorted(set(target) | set(indexes))

    def _record_failure(
        self,
        error: Exception,
        *,
        offset: int,
        count: int,
        stage: str,
    ) -> None:
        self.last_failure_details.append({
            "start_index": offset,
            "count": count,
            "stage": stage,
            "error_type": type(error).__name__,
            "error": str(error)[:500],
        })

    def _fallback_leaf(
        self,
        target: str,
        *,
        source: str,
        glossary_terms: list[GlossaryTerm],
        style: str,
        context: str,
        book_synopsis: str,
        chapter_digest: str,
        narrative_facts: str,
        offset: int,
        policy_rejected: bool,
    ) -> str:
        kwargs = {
            "sources": [source],
            "glossary_terms": glossary_terms,
            "style": style,
            "context": context,
            "book_synopsis": book_synopsis,
            "chapter_digest": chapter_digest,
            "narrative_facts": narrative_facts,
        }
        if policy_rejected and self.fallback_client is not None:
            try:
                result = self._call(
                    self.fallback_client,
                    [target],
                    stage="PolisherPolicyFallback",
                    **kwargs,
                )
                self._extend_unique(self.last_policy_fallback_indexes, [offset])
                return result[0]
            except Exception as error:
                self._record_failure(
                    error,
                    offset=offset,
                    count=1,
                    stage="PolisherPolicyFallback",
                )

        if self.recovery_fallback_client is not None:
            try:
                result = self._call(
                    self.recovery_fallback_client,
                    [target],
                    stage="PolisherRecoveryFallback",
                    **kwargs,
                )
                self._extend_unique(self.last_recovery_fallback_indexes, [offset])
                return result[0]
            except Exception as error:
                self._record_failure(
                    error,
                    offset=offset,
                    count=1,
                    stage="PolisherRecoveryFallback",
                )

        self._extend_unique(self.last_failed_indexes, [offset])
        return target

    def _polish_partition(
        self,
        targets: list[str],
        *,
        sources: list[str],
        glossary_terms: list[GlossaryTerm],
        style: str,
        context: str,
        book_synopsis: str,
        chapter_digest: str,
        narrative_facts: str,
        offset: int,
        stage: str,
    ) -> list[str]:
        """批级失败递归二分；只有失败叶子才交给独立恢复模型。"""
        kwargs = {
            "sources": sources,
            "glossary_terms": glossary_terms,
            "style": style,
            "context": context,
            "book_synopsis": book_synopsis,
            "chapter_digest": chapter_digest,
            "narrative_facts": narrative_facts,
        }
        policy_rejected = False
        try:
            return self._call(self.client, targets, stage=stage, **kwargs)
        except ContentPolicyError as error:
            policy_rejected = True
            self._record_failure(
                error, offset=offset, count=len(targets), stage=stage
            )
            stripped_kwargs = {
                **kwargs,
                "context": "",
                "book_synopsis": "",
                "chapter_digest": "",
            }
            try:
                result = self._call(
                    self.client,
                    targets,
                    stage="PolisherContextFallback",
                    **stripped_kwargs,
                )
                self._extend_unique(
                    self.last_policy_context_fallback_indexes,
                    list(range(offset, offset + len(targets))),
                )
                return result
            except Exception as stripped_error:
                self._record_failure(
                    stripped_error,
                    offset=offset,
                    count=len(targets),
                    stage="PolisherContextFallback",
                )
        except Exception as error:
            self._record_failure(
                error, offset=offset, count=len(targets), stage=stage
            )

        if len(targets) > 1:
            midpoint = len(targets) // 2
            common = {
                "glossary_terms": glossary_terms,
                "style": style,
                "context": context,
                "book_synopsis": book_synopsis,
                "chapter_digest": chapter_digest,
                "narrative_facts": narrative_facts,
                "stage": "PolisherRetry",
            }
            return self._polish_partition(
                targets[:midpoint],
                sources=sources[:midpoint],
                offset=offset,
                **common,
            ) + self._polish_partition(
                targets[midpoint:],
                sources=sources[midpoint:],
                offset=offset + midpoint,
                **common,
            )

        return [self._fallback_leaf(
            targets[0],
            source=sources[0],
            glossary_terms=glossary_terms,
            style=style,
            context=context,
            book_synopsis=book_synopsis,
            chapter_digest=chapter_digest,
            narrative_facts=narrative_facts,
            offset=offset,
            policy_rejected=policy_rejected,
        )]

    def polish(
        self,
        targets: list[str],
        *,
        sources: list[str] | None = None,
        glossary_terms: list[GlossaryTerm] | None = None,
        style: str = "",
        context: str = "",
        book_synopsis: str = "",
        chapter_digest: str = "",
        narrative_facts: str = "",
    ) -> list[str]:
        """对照原文精修；批级失败二分，叶子失败才调用对应备用模型。"""
        self.last_failed_indexes = []
        self.last_policy_context_fallback_indexes = []
        self.last_policy_fallback_indexes = []
        self.last_recovery_fallback_indexes = []
        self.last_failure_details = []
        if not targets:
            return []
        sources = list(sources or [""] * len(targets))
        if len(sources) != len(targets):
            self.last_failed_indexes = list(range(len(targets)))
            self._record_failure(
                PolishResponseError(
                    f"精修原文与初译段数不符：{len(sources)} != {len(targets)}"
                ),
                offset=0,
                count=len(targets),
                stage="PolisherInput",
            )
            return list(targets)
        terms = glossary_terms or []
        return self._polish_partition(
            targets,
            sources=sources,
            glossary_terms=terms,
            style=style,
            context=context,
            book_synopsis=book_synopsis,
            chapter_digest=chapter_digest,
            narrative_facts=narrative_facts,
            offset=0,
            stage="Polisher",
        )
