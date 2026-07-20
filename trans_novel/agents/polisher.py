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


class Polisher(Agent):
    def __init__(
        self,
        client: LLMClient,
        config: Config,
        *,
        fallback_client: LLMClient | None = None,
    ) -> None:
        super().__init__(client, config)
        self.fallback_client = fallback_client
        self.last_policy_fallback_indexes: list[int] = []
        self.last_failed_indexes: list[int] = []

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
    ) -> list[str] | None:
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
        if isinstance(items, list) and len(items) == n:
            polished = [str(item).strip() for item in items]
            if all(polished):
                return polished
        return None

    def _polish_after_policy_rejection(
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
    ) -> list[str]:
        """逐段定位 policy 拒绝，只把仍被拒绝的段落交给备用客户端。"""
        polished: list[str] = []
        for index, (source, target) in enumerate(zip(sources, targets)):
            kwargs = {
                "sources": [source],
                "glossary_terms": glossary_terms,
                "style": style,
                "context": context,
                "book_synopsis": book_synopsis,
                "chapter_digest": chapter_digest,
                "narrative_facts": narrative_facts,
            }
            try:
                result = self._call(
                    self.client,
                    [target],
                    stage="Polisher",
                    **kwargs,
                )
            except ContentPolicyError:
                result = None
                if self.fallback_client is not None:
                    try:
                        result = self._call(
                            self.fallback_client,
                            [target],
                            stage="PolisherPolicyFallback",
                            **kwargs,
                        )
                    except Exception:
                        result = None
                    if result:
                        self.last_policy_fallback_indexes.append(index)
            except Exception:
                result = None
            if not result:
                self.last_failed_indexes.append(index)
            polished.append(result[0] if result else target)
        return polished

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
        """对照原文精修；policy 拒绝时逐段定位并只回退问题段。"""
        if not targets:
            return []
        sources = list(sources or [""] * len(targets))
        self.last_failed_indexes = []
        self.last_policy_context_fallback_indexes: list[int] = []
        if len(sources) != len(targets):
            self.last_failed_indexes = list(range(len(targets)))
            return list(targets)
        terms = glossary_terms or []
        self.last_policy_fallback_indexes = []
        try:
            result = self._call(
                self.client,
                targets,
                sources=sources,
                glossary_terms=terms,
                style=style,
                context=context,
                book_synopsis=book_synopsis,
                chapter_digest=chapter_digest,
                narrative_facts=narrative_facts,
                stage="Polisher",
            )
        except ContentPolicyError:
            try:
                result = self._call(
                    self.client,
                    targets,
                    sources=sources,
                    glossary_terms=terms,
                    style=style,
                    context="",
                    book_synopsis="",
                    chapter_digest="",
                    narrative_facts=narrative_facts,
                    stage="PolisherContextFallback",
                )
            except ContentPolicyError:
                result = None
            except Exception:
                result = None
            if result is not None:
                self.last_policy_context_fallback_indexes = list(
                    range(len(targets))
                )
                return result
            return self._polish_after_policy_rejection(
                targets,
                sources=sources,
                glossary_terms=terms,
                style=style,
                context=context,
                book_synopsis=book_synopsis,
                chapter_digest=chapter_digest,
                narrative_facts=narrative_facts,
            )
        except Exception:
            result = None
        if result is None:
            self.last_failed_indexes = list(range(len(targets)))
            return list(targets)
        return result
