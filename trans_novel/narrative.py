"""Position-aware narrative knowledge for translation and review.

The glossary answers "how is this surface form translated?".  This module
answers the different question "which identity facts are safe to know here?".
Facts and alias links are projected at a chapter/segment position so a later
reveal cannot silently leak into an earlier translation prompt.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from dataclasses import dataclass, replace
from typing import Any, Iterable

from .glossary.store import GlossaryStore, GlossaryTerm, TYPE_PERSON

_TRUSTED = {"confirmed", "verified"}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS narrative_entities (
    entity_id        TEXT PRIMARY KEY,
    canonical_target TEXT NOT NULL,
    entity_type      TEXT NOT NULL DEFAULT '人物',
    note             TEXT,
    status           TEXT NOT NULL DEFAULT 'confirmed',
    updated_at       REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS narrative_aliases (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    source                TEXT NOT NULL,
    entity_id             TEXT NOT NULL,
    target                TEXT,
    visible_from_chapter  INTEGER NOT NULL DEFAULT 0,
    visible_from_segment  INTEGER NOT NULL DEFAULT 0,
    visible_until_chapter INTEGER,
    visible_until_segment INTEGER,
    evidence              TEXT,
    status                TEXT NOT NULL DEFAULT 'confirmed',
    updated_at            REAL NOT NULL,
    UNIQUE(source, entity_id, visible_from_chapter, visible_from_segment)
);
CREATE TABLE IF NOT EXISTS narrative_facts (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id             TEXT NOT NULL,
    predicate             TEXT NOT NULL,
    value                 TEXT NOT NULL,
    evidence              TEXT NOT NULL,
    evidence_chapter      INTEGER NOT NULL,
    evidence_segment      INTEGER NOT NULL DEFAULT 0,
    visible_from_chapter  INTEGER NOT NULL,
    visible_from_segment  INTEGER NOT NULL DEFAULT 0,
    visible_until_chapter INTEGER,
    visible_until_segment INTEGER,
    confidence            TEXT NOT NULL DEFAULT 'confirmed',
    status                TEXT NOT NULL DEFAULT 'confirmed',
    updated_at            REAL NOT NULL,
    UNIQUE(entity_id, predicate, value, evidence_chapter, evidence_segment)
);
CREATE INDEX IF NOT EXISTS idx_narrative_alias_source
    ON narrative_aliases(source);
CREATE INDEX IF NOT EXISTS idx_narrative_fact_entity
    ON narrative_facts(entity_id, predicate);
"""


@dataclass(frozen=True, order=True)
class NarrativePosition:
    chapter: int
    segment: int = 0


@dataclass(frozen=True)
class NarrativeFact:
    entity_id: str
    entity_target: str
    predicate: str
    value: str
    evidence: str
    visible_from: NarrativePosition
    visible_until: NarrativePosition | None = None
    confidence: str = "confirmed"
    status: str = "confirmed"


@dataclass(frozen=True)
class KnowledgeView:
    """The complete prompt-safe projection for one source span."""

    terms: list[GlossaryTerm]
    facts: list[NarrativeFact]

    def render_facts(self) -> str:
        if not self.facts:
            return "（暂无已确认且在当前位置可见的人物事实）"
        labels = {"gender": "性别", "identity": "身份", "role": "身份/职务"}
        return "\n".join(
            f"- {fact.entity_target}：{labels.get(fact.predicate, fact.predicate)}={fact.value}"
            f"（原文证据：{fact.evidence}）"
            for fact in self.facts
        )


def _entity_id(source: str) -> str:
    digest = hashlib.sha256(source.strip().encode("utf-8")).hexdigest()[:16]
    return f"person:{digest}"


def _int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _active(
    position: NarrativePosition,
    start_chapter: int,
    start_segment: int,
    end_chapter: int | None,
    end_segment: int | None,
) -> bool:
    if position < NarrativePosition(start_chapter, start_segment):
        return False
    if end_chapter is None:
        return True
    return position <= NarrativePosition(
        end_chapter,
        end_segment if end_segment is not None else 2**31 - 1,
    )


class NarrativeKnowledge:
    """Deep Module that owns entity linking and time-scoped fact projection."""

    def __init__(self, glossary: GlossaryStore) -> None:
        self.glossary = glossary
        self.conn = glossary.conn
        self.conn.executescript(_SCHEMA)
        alias_columns = {
            row["name"]
            for row in self.conn.execute("PRAGMA table_info(narrative_aliases)")
        }
        if "target" not in alias_columns:
            self.conn.execute("ALTER TABLE narrative_aliases ADD COLUMN target TEXT")
        self.conn.commit()

    def upsert_entity(
        self,
        entity_id: str,
        canonical_target: str,
        *,
        entity_type: str = TYPE_PERSON,
        note: str = "",
        status: str = "confirmed",
    ) -> None:
        now = time.time()
        self.conn.execute(
            """INSERT INTO narrative_entities
               (entity_id,canonical_target,entity_type,note,status,updated_at)
               VALUES (?,?,?,?,?,?)
               ON CONFLICT(entity_id) DO UPDATE SET
                 canonical_target=excluded.canonical_target,
                 entity_type=excluded.entity_type,
                 note=CASE WHEN excluded.note<>'' THEN excluded.note ELSE note END,
                 status=excluded.status, updated_at=excluded.updated_at""",
            (entity_id, canonical_target, entity_type, note, status, now),
        )
        self.conn.commit()

    def register_alias(
        self,
        source: str,
        entity_id: str,
        *,
        target: str = "",
        visible_from: NarrativePosition = NarrativePosition(0, 0),
        visible_until: NarrativePosition | None = None,
        evidence: str = "",
        status: str = "confirmed",
    ) -> None:
        if not self.conn.execute(
            "SELECT 1 FROM narrative_entities WHERE entity_id=?", (entity_id,)
        ).fetchone():
            raise ValueError(f"叙事实体不存在：{entity_id}")
        if visible_until is not None and visible_until < visible_from:
            raise ValueError("别名可见结束位置不能早于开始位置")
        self.conn.execute(
            """INSERT INTO narrative_aliases
               (source,entity_id,target,visible_from_chapter,visible_from_segment,
                visible_until_chapter,visible_until_segment,evidence,status,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(source,entity_id,visible_from_chapter,visible_from_segment)
               DO UPDATE SET visible_until_chapter=excluded.visible_until_chapter,
                 visible_until_segment=excluded.visible_until_segment,
                 target=CASE WHEN excluded.target<>'' THEN excluded.target ELSE target END,
                 evidence=excluded.evidence,status=excluded.status,
                 updated_at=excluded.updated_at""",
            (
                source,
                entity_id,
                target,
                visible_from.chapter,
                visible_from.segment,
                visible_until.chapter if visible_until else None,
                visible_until.segment if visible_until else None,
                evidence,
                status,
                time.time(),
            ),
        )
        self.conn.commit()

    def add_fact(
        self,
        entity_id: str,
        predicate: str,
        value: str,
        *,
        evidence: str,
        evidence_at: NarrativePosition,
        visible_from: NarrativePosition | None = None,
        visible_until: NarrativePosition | None = None,
        confidence: str = "confirmed",
        status: str = "confirmed",
    ) -> None:
        if not evidence.strip():
            raise ValueError("叙事事实必须附原文证据")
        if confidence not in _TRUSTED or status not in _TRUSTED:
            raise ValueError("只有 confirmed/verified 事实可以进入叙事知识库")
        start = visible_from or evidence_at
        if start < evidence_at:
            raise ValueError("叙事事实不能在原文证据出现前生效")
        if visible_until is not None and visible_until < start:
            raise ValueError("叙事事实结束位置不能早于开始位置")
        if not self.conn.execute(
            "SELECT 1 FROM narrative_entities WHERE entity_id=?", (entity_id,)
        ).fetchone():
            raise ValueError(f"叙事实体不存在：{entity_id}")
        self.conn.execute(
            """INSERT INTO narrative_facts
               (entity_id,predicate,value,evidence,evidence_chapter,evidence_segment,
                visible_from_chapter,visible_from_segment,visible_until_chapter,
                visible_until_segment,confidence,status,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(entity_id,predicate,value,evidence_chapter,evidence_segment)
               DO UPDATE SET evidence=excluded.evidence,
                 visible_from_chapter=excluded.visible_from_chapter,
                 visible_from_segment=excluded.visible_from_segment,
                 visible_until_chapter=excluded.visible_until_chapter,
                 visible_until_segment=excluded.visible_until_segment,
                 confidence=excluded.confidence,status=excluded.status,
                 updated_at=excluded.updated_at""",
            (
                entity_id,
                predicate,
                value,
                evidence,
                evidence_at.chapter,
                evidence_at.segment,
                start.chapter,
                start.segment,
                visible_until.chapter if visible_until else None,
                visible_until.segment if visible_until else None,
                confidence,
                status,
                time.time(),
            ),
        )
        self.conn.commit()

    def seed_character(self, character: dict[str, Any]) -> bool:
        """Persist one analyzer character only when its claims are locatable."""
        source = str(character.get("source", "") or "").strip()
        target = str(character.get("target", "") or "").strip()
        if not source or not target:
            return False
        entity_id = str(character.get("entity_id", "") or "").strip() or _entity_id(source)
        self.upsert_entity(
            entity_id,
            target,
            note=str(character.get("voice", "") or "").strip(),
        )
        self.register_alias(source, entity_id, target=target)

        for alias in character.get("aliases") or []:
            if isinstance(alias, str):
                # A bare alias from a whole-book model has no evidence or
                # reveal position, so linking it could leak a later identity.
                continue
            elif isinstance(alias, dict):
                alias_source = str(alias.get("source", "") or "").strip()
                start_chapter = _int(alias.get("visible_from_chapter"))
                start_segment = _int(alias.get("visible_from_segment")) or 0
                if start_chapter is None:
                    continue
                start = NarrativePosition(start_chapter, start_segment)
                end_chapter = _int(alias.get("visible_until_chapter"))
                end = (
                    NarrativePosition(
                        end_chapter,
                        _int(alias.get("visible_until_segment")) or 0,
                    )
                    if end_chapter is not None
                    else None
                )
                evidence = str(alias.get("evidence", "") or "").strip()
                alias_target = str(alias.get("target", "") or target).strip()
                status = str(alias.get("status", "confirmed") or "confirmed").lower()
            else:
                continue
            if alias_source and evidence and status in _TRUSTED:
                self.register_alias(
                    alias_source,
                    entity_id,
                    target=alias_target,
                    visible_from=start,
                    visible_until=end,
                    evidence=evidence,
                    status=status,
                )

        confidence = str(character.get("gender_confidence", "") or "").lower()
        evidence = str(character.get("gender_evidence", "") or "").strip()
        evidence_chapter = _int(character.get("gender_evidence_chapter"))
        evidence_segment = _int(character.get("gender_evidence_segment")) or 0
        gender = str(character.get("gender", "") or "").strip()
        if (
            confidence in _TRUSTED
            and evidence
            and evidence_chapter is not None
            and gender
            and gender not in {"未知", "unknown"}
        ):
            at = NarrativePosition(evidence_chapter, evidence_segment)
            self.add_fact(
                entity_id,
                "gender",
                gender,
                evidence=evidence,
                evidence_at=at,
                visible_from=at,
                confidence=confidence,
                status=confidence,
            )
        return True

    def seed_from_analysis(self, analysis: dict[str, Any]) -> int:
        return sum(
            1
            for character in analysis.get("characters") or []
            if isinstance(character, dict) and self.seed_character(character)
        )

    def digest(self) -> str:
        """Return a stable fingerprint so cached reviews follow fact changes."""
        payload: dict[str, list[dict[str, Any]]] = {}
        ordering = {
            "narrative_entities": "entity_id",
            "narrative_aliases": "id",
            "narrative_facts": "id",
        }
        for table, order_by in ordering.items():
            rows = self.conn.execute(
                f"SELECT * FROM {table} ORDER BY {order_by}"
            ).fetchall()
            payload[table] = [
                {key: row[key] for key in row.keys() if key != "updated_at"}
                for row in rows
            ]
        encoded = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def change_points(self, chapter: int, start: int, end: int) -> list[int]:
        """Return segment positions where the visible knowledge projection changes."""
        points: set[int] = set()
        for table in ("narrative_aliases", "narrative_facts"):
            rows = self.conn.execute(
                f"""SELECT visible_from_chapter,visible_from_segment,
                           visible_until_chapter,visible_until_segment
                    FROM {table}
                    WHERE status IN ('confirmed','verified')"""
            ).fetchall()
            for row in rows:
                if row["visible_from_chapter"] == chapter:
                    point = row["visible_from_segment"]
                    if start < point < end:
                        points.add(point)
                if row["visible_until_chapter"] == chapter:
                    until = row["visible_until_segment"]
                    if until is not None and start < until + 1 < end:
                        points.add(until + 1)
        return sorted(points)

    def view(
        self,
        text: str,
        position: NarrativePosition,
        *,
        fallback_terms: Iterable[GlossaryTerm] | None = None,
    ) -> KnowledgeView:
        """Return terms and non-conflicting facts visible at ``position``."""
        base_terms = list(fallback_terms) if fallback_terms is not None else self.glossary.all_terms()
        relevant = GlossaryStore.terms_in(base_terms, text)
        relevant_keys = {term.source for term in relevant}
        matched_sources = set(relevant_keys)
        for term in relevant:
            matched_sources.update(alias for alias in term.aliases if alias and alias in text)

        rows = self.conn.execute(
            """SELECT a.*,e.canonical_target
               FROM narrative_aliases a
               JOIN narrative_entities e ON e.entity_id=a.entity_id
               WHERE a.status IN ('confirmed','verified')"""
        ).fetchall()
        entity_targets: dict[str, str] = {}
        alias_targets: dict[str, str] = {}
        source_entities: dict[str, set[str]] = {}
        for row in rows:
            source = row["source"]
            if source not in matched_sources and source not in text:
                continue
            if not _active(
                position,
                row["visible_from_chapter"],
                row["visible_from_segment"],
                row["visible_until_chapter"],
                row["visible_until_segment"],
            ):
                continue
            source_entities.setdefault(source, set()).add(row["entity_id"])
            entity_targets[row["entity_id"]] = row["canonical_target"]
            alias_targets[source] = row["target"] or row["canonical_target"]

        # Ambiguous role words deliberately inherit no person fact.
        active_entities = {
            next(iter(entity_ids))
            for entity_ids in source_entities.values()
            if len(entity_ids) == 1
        }
        fact_rows = self.conn.execute(
            """SELECT * FROM narrative_facts
               WHERE confidence IN ('confirmed','verified')
                 AND status IN ('confirmed','verified')"""
        ).fetchall()
        candidates: list[NarrativeFact] = []
        for row in fact_rows:
            if row["entity_id"] not in active_entities:
                continue
            if not _active(
                position,
                row["visible_from_chapter"],
                row["visible_from_segment"],
                row["visible_until_chapter"],
                row["visible_until_segment"],
            ):
                continue
            candidates.append(
                NarrativeFact(
                    entity_id=row["entity_id"],
                    entity_target=entity_targets[row["entity_id"]],
                    predicate=row["predicate"],
                    value=row["value"],
                    evidence=row["evidence"],
                    visible_from=NarrativePosition(
                        row["visible_from_chapter"], row["visible_from_segment"]
                    ),
                    visible_until=(
                        NarrativePosition(
                            row["visible_until_chapter"],
                            (
                                row["visible_until_segment"]
                                if row["visible_until_segment"] is not None
                                else 2**31 - 1
                            ),
                        )
                        if row["visible_until_chapter"] is not None
                        else None
                    ),
                    confidence=row["confidence"],
                    status=row["status"],
                )
            )

        grouped: dict[tuple[str, str], list[NarrativeFact]] = {}
        for fact in candidates:
            grouped.setdefault((fact.entity_id, fact.predicate), []).append(fact)
        facts = [
            values[0]
            for values in grouped.values()
            if len({fact.value for fact in values}) == 1
        ]
        facts.sort(key=lambda fact: (fact.entity_target, fact.predicate, fact.value))

        gender_by_entity = {
            fact.entity_id: fact.value for fact in facts if fact.predicate == "gender"
        }
        projected_terms: list[GlossaryTerm] = []
        projected_base_sources = {term.source for term in base_terms}
        dynamic_terms = [
            GlossaryTerm(
                source=source,
                target=alias_targets[source],
                type=TYPE_PERSON,
                status="confirmed",
            )
            for source, entity_ids in source_entities.items()
            if source not in projected_base_sources and len(entity_ids) == 1
        ]
        for term in [*base_terms, *dynamic_terms]:
            entity_ids = source_entities.get(term.source, set())
            if term.source not in relevant_keys:
                projected_terms.append(replace(term, gender=""))
            elif len(entity_ids) == 1:
                gender = gender_by_entity.get(next(iter(entity_ids)), "")
                projected_terms.append(
                    replace(
                        term,
                        gender=gender,
                        status="confirmed" if gender else term.status,
                    )
                )
            elif term.gender and term.status in _TRUSTED and not entity_ids:
                # Backward compatibility for manually verified legacy tables.
                projected_terms.append(term)
            else:
                projected_terms.append(replace(term, gender=""))
        return KnowledgeView(projected_terms, facts)
