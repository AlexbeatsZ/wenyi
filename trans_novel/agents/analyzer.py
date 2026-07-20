"""全局分析 Agent（强档）。

通读样章，产出风格指南、角色圣经（含性别/语气）、初始术语候选，
并把角色/术语种入术语库，作为全书翻译的统一基准。
"""

from __future__ import annotations

from typing import Any

from ..glossary.store import GlossaryStore, GlossaryTerm, TYPE_PERSON
from ..narrative import NarrativeKnowledge
from . import prompts
from .base import Agent


def _text(value: Any, default: str = "") -> str:
    """把模型字段规整为文本；嵌套对象等非标量值直接回退。"""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return default


class Analyzer(Agent):
    def analyze(self, sample_text: str) -> dict[str, Any]:
        """分析样本文本，并返回经过类型清洗的风格、角色和术语信息。"""
        system = prompts.render("analyzer_system", src=self.src, tgt=self.tgt)
        user = prompts.render("analyzer_user", src=self.src, tgt=self.tgt,
                              sample=sample_text)
        # 不传 default：分析失败照常抛出，由调用方决定（prepare 阶段失败应显式暴露）
        data = self._ask_json(system, user, tier="strong")
        if not isinstance(data, dict):
            data = {}
        for key in (
            "genre",
            "tone",
            "style_guide",
            "narration",
            "pacing",
            "register",
            "dialogue_style",
            "rhetoric",
        ):
            data[key] = _text(data.get(key))
        data["characters"] = self.dict_items(data.get("characters"))
        data["terms"] = self.dict_items(data.get("terms"))
        return data

    def seed_glossary(self, store: GlossaryStore, analysis: dict[str, Any]) -> int:
        """把分析得到的角色/术语种入术语库，返回写入条目数。"""
        count = 0
        knowledge = NarrativeKnowledge(store)
        for ch in self.dict_items(analysis.get("characters")):
            source = _text(ch.get("source"))
            target = _text(ch.get("target"))
            if not source or not target:
                continue
            confidence = _text(ch.get("gender_confidence")).lower()
            evidence = _text(ch.get("gender_evidence"))
            evidence_chapter = ch.get("gender_evidence_chapter")
            confirmed = (
                confidence in {"confirmed", "verified"}
                and bool(evidence)
                and isinstance(evidence_chapter, int)
                and not isinstance(evidence_chapter, bool)
                and evidence_chapter >= 0
            )
            note = _text(ch.get("note"))
            if evidence:
                note = f"{note}；性别证据：{evidence}" if note else f"性别证据：{evidence}"
            store.upsert_term(
                GlossaryTerm(
                    source=source,
                    target=target,
                    reading=_text(ch.get("reading")),
                    type=TYPE_PERSON,
                    gender=_text(ch.get("gender")) if confirmed else "",
                    note=note,
                    # Identity aliases are time-scoped by NarrativeKnowledge;
                    # a flat glossary alias would make a later reveal global.
                    aliases=[],
                    status="confirmed" if confirmed else "ok",
                    first_chapter=0,
                ),
                chapter=0,
            )
            knowledge.seed_character(ch)
            count += 1
        for tm in self.dict_items(analysis.get("terms")):
            source = _text(tm.get("source"))
            target = _text(tm.get("target"))
            if not source or not target:
                continue
            store.upsert_term(
                GlossaryTerm(
                    source=source,
                    target=target,
                    reading=_text(tm.get("reading")),
                    type=_text(tm.get("type"), "术语"),
                    note=_text(tm.get("note")),
                    first_chapter=0,
                ),
                chapter=0,
            )
            count += 1
        return count

    def style_brief(
        self,
        analysis: dict[str, Any],
        *,
        include_character_facts: bool = False,
    ) -> str:
        """把分析结果浓缩为风格简报，默认排除可能剧透的人物事实。"""
        lines = []
        if analysis.get("genre"):
            lines.append(f"体裁：{analysis['genre']}")
        if analysis.get("tone"):
            lines.append(f"语气文体：{analysis['tone']}")
        if analysis.get("style_guide"):
            lines.append(f"风格指南：{analysis['style_guide']}")
        # 细粒度风格维度（旧 analysis.json 缺字段时自动跳过，向后兼容）
        for key, tag in (("narration", "叙事"), ("pacing", "句式节奏"),
                         ("register", "语域"), ("dialogue_style", "对话风格"),
                         ("rhetoric", "修辞")):
            if analysis.get(key):
                lines.append(f"{tag}：{analysis[key]}")
        chars = [
            character
            for character in self.dict_items(analysis.get("characters"))
            if include_character_facts or _text(character.get("voice"))
        ]
        if chars:
            lines.append("角色：")
            for c in chars:
                gender_is_confirmed = (
                    include_character_facts
                    and
                    _text(c.get("gender_confidence")).lower() == "confirmed"
                )
                g = (
                    f"，{c.get('gender')}"
                    if c.get("gender") and gender_is_confirmed
                    else ""
                )
                detail = c.get("note") if include_character_facts else c.get("voice")
                note = f"，{detail}" if detail else ""
                lines.append(f"  - {c.get('target', c.get('source',''))}({c.get('source','')}{g}{note})")
        return "\n".join(lines)
