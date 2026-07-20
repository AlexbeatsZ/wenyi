"""修复既有 Wenyi 状态中的对话引号，并可沿用源文直角引号样式。"""

from __future__ import annotations

import argparse
import json
import os
import shutil
from collections import Counter
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from trans_novel.postprocess.punct import (
    QuoteStyle,
    apply_source_quote_style,
    restore_zh_dialogue_quotes,
)


def _chapter_index(path: Path) -> int:
    try:
        return int(path.stem.removeprefix("ch"))
    except ValueError:
        return 10**9


def _backup_root() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        raise RuntimeError("缺少 LOCALAPPDATA，无法按约定创建安全备份")
    return Path(local_app_data) / "Temp" / ".agents"


def _atomic_write_json(path: Path, payload: dict, temp_root: Path) -> None:
    temp_root.mkdir(parents=True, exist_ok=True)
    temporary = temp_root / f"{path.name}.{uuid4().hex}.tmp"
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def repair_run(
    run_dir: Path,
    *,
    apply: bool,
    quote_style: QuoteStyle = "source",
) -> tuple[int, int, Path | None]:
    """扫描或修复一个运行目录，返回章节数、segment 数和备份路径。"""
    run_dir = run_dir.expanduser().resolve()
    chapters_dir = run_dir / "chapters"
    if not (run_dir / "manifest.json").is_file() or not chapters_dir.is_dir():
        raise ValueError(f"不是有效的 Wenyi 运行目录：{run_dir}")

    pending: list[tuple[Path, dict, list[tuple[int, str, str]]]] = []
    for path in sorted(chapters_dir.glob("ch*.json"), key=_chapter_index):
        payload = json.loads(path.read_text(encoding="utf-8"))
        segments = payload.get("segments", [])
        sources = [str(segment.get("source") or "") for segment in segments]
        targets = [str(segment.get("target") or "") for segment in segments]
        continuations = [bool(segment.get("cont")) for segment in segments]
        styled = (
            apply_source_quote_style(sources, targets)
            if quote_style == "source"
            else targets
        )
        restored = restore_zh_dialogue_quotes(
            sources,
            styled,
            continuations,
            quote_style=quote_style,
        )
        changes = [
            (index, before, after)
            for index, (before, after) in enumerate(zip(targets, restored))
            if before != after
        ]
        if changes:
            pending.append((path, payload, changes))

    changed_segments = sum(len(changes) for _, _, changes in pending)
    backup_dir: Path | None = None
    if apply and pending:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup_dir = _backup_root() / f"wenyi-quote-repair-{stamp}" / run_dir.name
        if backup_dir.exists():
            raise FileExistsError(f"备份目录已存在：{backup_dir}")
        shutil.copytree(run_dir, backup_dir)
        temp_root = _backup_root() / "wenyi-quote-repair-tmp"
        for path, payload, changes in pending:
            for index, _, after in changes:
                payload["segments"][index]["target"] = after
            _atomic_write_json(path, payload, temp_root)

    counts: Counter[int] = Counter()
    for path, _, changes in pending:
        counts[_chapter_index(path)] += len(changes)
    for chapter, count in sorted(counts.items()):
        print(f"ch{chapter}: {count} 个 segment")
    mode = "已修复" if apply else "待修复"
    print(f"{mode}：{len(pending)} 章，{changed_segments} 个 segment")
    if backup_dir is not None:
        print(f"备份：{backup_dir}")
    return len(pending), changed_segments, backup_dir


def main() -> None:
    parser = argparse.ArgumentParser(
        description="依据 source 补回对话引号，并按配置沿用「」『』或转换为“”‘’。"
    )
    parser.add_argument("run_dir", type=Path, help="包含 manifest.json 的状态目录")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="创建完整备份后写回；省略时仅 dry-run",
    )
    parser.add_argument(
        "--quote-style",
        choices=("source", "zh-cn"),
        default="source",
        help="source=沿用源文直角引号（默认）；zh-cn=使用大陆式弯引号",
    )
    args = parser.parse_args()
    repair_run(args.run_dir, apply=args.apply, quote_style=args.quote_style)


if __name__ == "__main__":
    main()
