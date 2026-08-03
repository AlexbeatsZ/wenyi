#!/usr/bin/env python3
"""Read-only live dashboard and reader for an existing Wenyi run directory.

This sidecar deliberately has no dependency on ``trans_novel``.  It reads the
persisted JSON/JSONL artifacts and never opens them for writing.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import sys
import threading
import time
import webbrowser
from collections import Counter
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse


ROOT = Path(__file__).resolve().parent

STAGE_EXPLANATIONS = {
    "translation": "逐段读取原文，结合章节上下文、术语和已知叙事事实生成初译。",
    "polish": "对照原文与初译逐段精修，重点改善中文表达，同时保持信息和段落一一对应。",
    "review": "独立比对原文与定稿，检查漏译、增译、误译、术语、人称和语境问题；严重项会尝试自动修复。",
    "idle": "当前持久化状态中没有正在处理的章节。",
}

EVENT_LABELS = {
    "autofix_applied": "自动修复已应用",
    "autofix_rejected": "自动修复被拒绝",
    "chapter_reviewed": "章节终审完成",
    "chapter_review_failed": "章节终审失败",
    "book_review_started": "全书终审开始",
    "book_review_finished": "全书终审完成",
    "batch_translated": "批次初译/精修完成",
    "batch_repolished": "精修重试完成",
    "batch_skipped": "已完成批次复用",
    "chapter_done": "章节翻译完成",
    "chapter_glossary_extracted": "章节术语提取完成",
    "batch_glossary_extracted": "批次术语提取完成",
    "glossary_conflict_resolved_by_model": "术语冲突已裁决",
    "translate_run_started": "翻译流程开始",
    "translate_run_finished": "翻译流程完成",
    "run_resumed": "流程恢复运行",
    "usage_summary": "用量检查点",
    "report_saved": "报告已保存",
    "assembled": "成品已导出",
}


def _read_json(path: Path, default: Any) -> Any:
    """Read a replace-written JSON file without ever locking or modifying it."""
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (FileNotFoundError, PermissionError, json.JSONDecodeError, OSError):
        return default


def _parse_ts(value: Any) -> float:
    if not isinstance(value, str) or not value:
        return 0.0
    try:
        return datetime.fromisoformat(value).timestamp()
    except ValueError:
        return 0.0


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _has_text(value: Any) -> bool:
    return value is not None and bool(str(value).strip())


def _mtime_iso(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime).astimezone().isoformat(
        timespec="seconds"
    )


class EventCache:
    """Cache the append-only event stream until size or mtime changes."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._signature: tuple[int, int] | None = None
        self._events: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    def read(self) -> list[dict[str, Any]]:
        try:
            stat = self.path.stat()
            signature = (stat.st_mtime_ns, stat.st_size)
        except OSError:
            return []
        with self._lock:
            if signature == self._signature:
                return self._events
            events: list[dict[str, Any]] = []
            try:
                with self.path.open("r", encoding="utf-8") as handle:
                    for line in handle:
                        try:
                            item = json.loads(line)
                        except json.JSONDecodeError:
                            # The writer may currently be appending the final line.
                            continue
                        if isinstance(item, dict):
                            events.append(item)
            except (FileNotFoundError, PermissionError, OSError):
                return self._events
            self._events = events
            self._signature = signature
            return events


def _yaml_section(text: str, name: str) -> str:
    match = re.search(rf"(?m)^{re.escape(name)}:\s*(?:#.*)?$", text)
    if not match:
        return ""
    start = match.end()
    tail = text[start:]
    end = re.search(r"(?m)^[A-Za-z_][\w-]*:\s*(?:#.*)?$", tail)
    return tail[: end.start()] if end else tail


def _config_models(path: Path | None) -> dict[str, dict[str, str]]:
    if path is None:
        return {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    result: dict[str, dict[str, str]] = {}
    for key in (
        "translation_llm",
        "llm",
        "review_llm",
        "content_fallback_llm",
        "polish_fallback_llm",
    ):
        section = _yaml_section(text, key)
        if not section:
            continue
        provider = re.search(r"(?m)^\s+provider:\s*([^#\r\n]+)", section)
        models = re.findall(r"(?m)^\s+model:\s*([^#\r\n]+)", section)
        result[key] = {
            "provider": provider.group(1).strip(" \t'\"") if provider else "",
            "model": models[0].strip(" \t'\"") if models else "",
        }
    return result


def _chapter_index(chapter: dict[str, Any]) -> int:
    return _safe_int(chapter.get("index"), -1)


def _current_work(manifest: dict[str, Any], models: dict[str, dict[str, str]]) -> dict[str, Any]:
    chapters = [item for item in manifest.get("chapters", []) if isinstance(item, dict)]
    running_review = next((c for c in chapters if c.get("review_status") == "running"), None)
    if running_review:
        model = models.get("review_llm", {})
        return {
            "stage": "review",
            "stage_label": "独立终审与严重项修复",
            "chapter": _chapter_index(running_review),
            "title": running_review.get("title", ""),
            "model": model.get("model", ""),
            "provider": model.get("provider", ""),
            "explanation": STAGE_EXPLANATIONS["review"],
            "precision": "当前状态只能确认到章节；并行审查的具体段块会在模型返回并落盘后出现。",
        }
    translating = next((c for c in chapters if c.get("status") != "done"), None)
    if translating:
        model = models.get("translation_llm", {})
        return {
            "stage": "translation",
            "stage_label": "初译与精修",
            "chapter": _chapter_index(translating),
            "title": translating.get("title", ""),
            "model": model.get("model", ""),
            "provider": model.get("provider", ""),
            "explanation": STAGE_EXPLANATIONS["translation"],
            "precision": "批次完成后会显示本批原文、译文、精修和回退结果。",
        }
    return {
        "stage": "idle",
        "stage_label": "空闲或阶段切换",
        "chapter": None,
        "title": "",
        "model": "",
        "provider": "",
        "explanation": STAGE_EXPLANATIONS["idle"],
        "precision": "以最近事件时间和正在运行的外部命令共同判断是否仍在切换阶段。",
    }


def _review_issue_state(events: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], Counter[str]]:
    latest_reviews: dict[int, dict[str, Any]] = {}
    fixes: dict[tuple[int, int], dict[str, Any]] = {}
    for event in events:
        kind = event.get("event")
        chapter = _safe_int(event.get("chapter"), -1)
        if kind == "chapter_reviewed" and chapter >= 0:
            latest_reviews[chapter] = event
        elif kind in {"autofix_applied", "autofix_rejected"}:
            index = _safe_int(event.get("index"), -1)
            if chapter >= 0 and index >= 0:
                fixes[(chapter, index)] = event

    rows: list[dict[str, Any]] = []
    type_counts: Counter[str] = Counter()
    for chapter, review in latest_reviews.items():
        review_ts = review.get("ts", "")
        for issue in review.get("issues", []):
            if not isinstance(issue, dict):
                continue
            index = _safe_int(issue.get("index"), -1)
            issue_type = str(issue.get("type", "other") or "other")
            type_counts[issue_type] += 1
            fix = fixes.get((chapter, index), {})
            rows.append({
                "chapter": chapter,
                "index": index,
                "type": issue_type,
                "detail": issue.get("detail", ""),
                "suggestion": issue.get("suggestion", ""),
                "fixed": bool(issue.get("fixed")),
                "fix_status": fix.get("event", ""),
                "source": fix.get("source", ""),
                "before": fix.get("before", ""),
                "after": fix.get("after", ""),
                "proposed": fix.get("proposed", ""),
                "ts": fix.get("ts", review_ts),
            })
    rows.sort(key=lambda row: (_parse_ts(row.get("ts")), row["chapter"], row["index"]), reverse=True)
    return rows, type_counts


def _event_detail(event: dict[str, Any]) -> str:
    kind = str(event.get("event", ""))
    chapter = event.get("chapter")
    index = event.get("index")
    prefix = ""
    if chapter is not None:
        prefix = f"第 {int(chapter) + 1} 章"
    if index is not None:
        prefix += f" · 段 {index}"
    if kind == "chapter_reviewed":
        return f"{prefix} · 发现 {event.get('issue_count', 0)} 个问题"
    if kind == "chapter_review_failed":
        return f"{prefix} · {event.get('error', '未知错误')}"
    if kind == "autofix_applied":
        issues = event.get("issues") or []
        detail = issues[0].get("detail", "") if issues and isinstance(issues[0], dict) else ""
        return f"{prefix} · {detail}"
    if kind == "autofix_rejected":
        issues = event.get("issues") or []
        detail = issues[0].get("detail", "") if issues and isinstance(issues[0], dict) else ""
        return f"{prefix} · 候选修复未通过保真检查 · {detail}"
    if kind in {"batch_translated", "batch_repolished", "batch_skipped"}:
        return f"{prefix} · 从段 {event.get('start_index', 0)} 开始，共 {event.get('count', 0)} 段"
    if kind == "usage_summary":
        delta = event.get("delta") or {}
        totals = delta.get("totals") or {}
        return f"{event.get('scope', '阶段')} · {totals.get('calls', 0)} 次调用 · {totals.get('total_tokens', 0):,} tokens"
    return prefix or str(event.get("reason", "") or "状态已更新")


class Observer:
    def __init__(self, run_dir: Path, config_path: Path | None = None) -> None:
        self.run_dir = run_dir.resolve()
        self.config_path = config_path.resolve() if config_path else None
        self.events = EventCache(self.run_dir / "events.jsonl")

    def book_payload(self) -> dict[str, Any]:
        """Return the previous reader's book semantics from the same run state."""
        manifest = _read_json(self.run_dir / "manifest.json", {})
        chapters: list[dict[str, Any]] = []
        total_segments = 0
        translated_segments = 0
        latest_path = self.run_dir / "manifest.json"
        for item in manifest.get("chapters", []):
            if not isinstance(item, dict):
                continue
            index = _safe_int(item.get("index"), len(chapters))
            path = self.run_dir / "chapters" / f"ch{index}.json"
            chapter = _read_json(path, {})
            segments = [
                segment
                for segment in chapter.get("segments", [])
                if isinstance(segment, dict) and _has_text(segment.get("source"))
            ]
            translated = sum(_has_text(segment.get("target")) for segment in segments)
            total = len(segments)
            total_segments += total
            translated_segments += translated
            try:
                if path.stat().st_mtime > latest_path.stat().st_mtime:
                    latest_path = path
                updated_at = _mtime_iso(path)
                revision = path.stat().st_mtime_ns
            except OSError:
                updated_at = ""
                revision = 0
            stored_status = str(item.get("status", "pending"))
            display_status = "done" if stored_status == "done" else (
                "translating" if translated else "pending"
            )
            chapters.append({
                "index": index,
                "title": str(item.get("title") or chapter.get("title") or f"章节 {index + 1}"),
                "status": display_status,
                "stored_status": stored_status,
                "review_status": str(item.get("review_status", "pending")),
                "translated_segments": translated,
                "total_segments": total,
                "updated_at": updated_at,
                "revision": revision,
            })
        done = sum(chapter["stored_status"] == "done" for chapter in chapters)
        reviewed = sum(chapter["review_status"] == "done" for chapter in chapters)
        failed = sum(chapter["review_status"] == "failed" for chapter in chapters)
        return {
            "title": str(manifest.get("title") or self.run_dir.name),
            "source_lang": str(manifest.get("source_lang", "")),
            "target_lang": str(manifest.get("target_lang", "")),
            "chapter_count": len(chapters),
            "done_chapters": done,
            "total_segments": total_segments,
            "translated_segments": translated_segments,
            "translation_complete": bool(chapters) and done == len(chapters),
            "review_done_chapters": reviewed,
            "review_failed_chapters": failed,
            "review_complete": bool(chapters) and reviewed == len(chapters),
            "updated_at": _mtime_iso(latest_path) if latest_path.is_file() else "",
            "chapters": chapters,
        }

    def chapter_payload(self, index: int) -> dict[str, Any]:
        """Return one translated chapter without exposing empty target segments."""
        manifest = _read_json(self.run_dir / "manifest.json", {})
        item = next((
            chapter for chapter in manifest.get("chapters", [])
            if isinstance(chapter, dict) and _safe_int(chapter.get("index"), -1) == index
        ), None)
        if item is None:
            raise KeyError(index)
        path = self.run_dir / "chapters" / f"ch{index}.json"
        chapter = _read_json(path, {})
        segments: list[dict[str, Any]] = []
        for raw in chapter.get("segments", []):
            if not isinstance(raw, dict) or not _has_text(raw.get("source")):
                continue
            target = raw.get("target")
            segments.append({
                "index": _safe_int(raw.get("index"), len(segments)),
                "kind": str(raw.get("kind", "text")),
                "source": str(raw.get("source", "")),
                "target": None if target is None else str(target),
                "translated": _has_text(target),
            })
        return {
            "index": index,
            "title": str(item.get("title") or chapter.get("title") or f"章节 {index + 1}"),
            "status": str(item.get("status", "pending")),
            "review_status": str(item.get("review_status", "pending")),
            "translated_segments": sum(segment["translated"] for segment in segments),
            "total_segments": len(segments),
            "updated_at": _mtime_iso(path) if path.is_file() else "",
            "revision": path.stat().st_mtime_ns if path.is_file() else 0,
            "segments": segments,
        }

    def snapshot(self, limit: int = 60) -> dict[str, Any]:
        manifest = _read_json(self.run_dir / "manifest.json", {})
        usage = _read_json(self.run_dir / "usage.json", {})
        events = self.events.read()
        models = _config_models(self.config_path)
        chapters = [item for item in manifest.get("chapters", []) if isinstance(item, dict)]
        translated = sum(item.get("status") == "done" for item in chapters)
        reviewed = sum(item.get("review_status") == "done" for item in chapters)
        review_failed = sum(item.get("review_status") == "failed" for item in chapters)
        issues, issue_types = _review_issue_state(events)
        latest_event = events[-1] if events else {}
        event_rows = []
        for event in reversed(events):
            kind = str(event.get("event", ""))
            if kind not in EVENT_LABELS:
                continue
            event_rows.append({
                "event": kind,
                "label": EVENT_LABELS[kind],
                "ts": event.get("ts", ""),
                "detail": _event_detail(event),
                "chapter": event.get("chapter"),
                "severity": "error" if kind.endswith("failed") else ("warning" if kind == "autofix_rejected" else "normal"),
            })
            if len(event_rows) >= limit:
                break
        totals = usage.get("totals") or {}
        fixed = sum(bool(row.get("fixed")) for row in issues)
        return {
            "book": manifest.get("title") or self.run_dir.name,
            "observed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "run_dir": str(self.run_dir),
            "last_event_at": latest_event.get("ts", ""),
            "last_event_age_seconds": max(0, round(time.time() - _parse_ts(latest_event.get("ts")))) if latest_event else None,
            "current": _current_work(manifest, models),
            "progress": {
                "chapters": len(chapters),
                "translated": translated,
                "reviewed": reviewed,
                "review_running": sum(item.get("review_status") == "running" for item in chapters),
                "review_failed": review_failed,
            },
            "quality": {
                "issues": len(issues),
                "fixed": fixed,
                "unfixed": len(issues) - fixed,
                "types": dict(issue_types.most_common()),
            },
            "usage": {
                "totals": totals,
                "by_stage": usage.get("by_stage") or {},
                "by_tier": usage.get("by_tier") or {},
            },
            "models": models,
            "issues": issues[:limit],
            "events": event_rows,
            "visibility": {
                "available": ["阶段与章节", "已落盘的原文/译文", "结构化审校理由", "修复前后", "错误与回退", "调用与 token"],
                "unavailable": "模型私有思维链不会由当前接口返回；这里不生成或猜测它。",
            },
        }


class Handler(BaseHTTPRequestHandler):
    observer: Observer

    def _send_json(self, data: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
        parsed = urlparse(self.path)
        if parsed.path == "/api/snapshot":
            query = parse_qs(parsed.query)
            limit = max(10, min(200, _safe_int((query.get("limit") or [60])[0], 60)))
            try:
                self._send_json(self.observer.snapshot(limit))
            except Exception as error:  # keep the observer from affecting the run
                self._send_json({"error": f"读取观察数据失败：{type(error).__name__}: {error}"}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        if parsed.path == "/api/book":
            try:
                self._send_json(self.observer.book_payload())
            except Exception as error:
                self._send_json({"error": f"读取书籍状态失败：{type(error).__name__}: {error}"}, HTTPStatus.SERVICE_UNAVAILABLE)
            return
        chapter_match = re.fullmatch(r"/api/chapters/(\d+)", parsed.path)
        if chapter_match:
            try:
                self._send_json(self.observer.chapter_payload(int(chapter_match.group(1))))
            except KeyError:
                self._send_json({"error": "chapter_not_found"}, HTTPStatus.NOT_FOUND)
            except Exception as error:
                self._send_json({"error": f"读取章节失败：{type(error).__name__}: {error}"}, HTTPStatus.SERVICE_UNAVAILABLE)
            return
        relative = "index.html" if parsed.path == "/" else parsed.path.lstrip("/")
        if relative not in {"index.html"}:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        path = ROOT / relative
        try:
            payload = path.read_bytes()
        except OSError:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", mimetypes.guess_type(path.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write("[%s] %s\n" % (self.log_date_time_string(), fmt % args))


def main() -> int:
    if os.name == "nt":
        # Keep Chinese help/status readable in modern PowerShell and redirected logs.
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(
        description="为指定 Wenyi 书籍启动只读的审核监控与情节阅读页面"
    )
    parser.add_argument(
        "run_dir",
        type=Path,
        help="包含 manifest.json、events.jsonl 和 chapters/ 的书籍状态目录",
    )
    parser.add_argument("--config", type=Path, help="可选：只读配置文件，用于显示各阶段模型")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--open",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="启动后打开默认浏览器；使用 --no-open 禁用（默认：打开）",
    )
    args = parser.parse_args()
    run_dir = args.run_dir.expanduser().resolve()
    if not (run_dir / "manifest.json").is_file():
        parser.error(f"找不到 {run_dir / 'manifest.json'}")
    config_path = args.config.expanduser().resolve() if args.config else None
    if config_path is not None and not config_path.is_file():
        parser.error(f"找不到配置文件：{config_path}")
    Handler.observer = Observer(run_dir, config_path)
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    url = f"http://{args.host}:{server.server_port}/"
    print(f"Wenyi Observer: {url}", flush=True)
    print(f"Read-only run directory: {run_dir}", flush=True)
    print("按 Ctrl+C 停止观察器。", flush=True)
    if args.open:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
