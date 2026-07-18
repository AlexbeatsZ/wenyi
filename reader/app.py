"""从 Wenyi 运行状态中只读提取译文并提供局域网阅读页面。"""

from __future__ import annotations

import argparse
import json
import re
import socket
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


APP_DIR = Path(__file__).resolve().parent
INDEX_PATH = APP_DIR / "index.html"


def _has_text(value: Any) -> bool:
    return value is not None and bool(str(value).strip())


def _mtime_iso(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime).astimezone().isoformat(
        timespec="seconds"
    )


class ReaderState:
    """一本 Wenyi 书籍状态目录的只读视图。"""

    def __init__(self, run_dir: Path):
        self.run_dir = run_dir.expanduser().resolve()
        self.manifest_path = self.run_dir / "manifest.json"
        self.chapters_dir = self.run_dir / "chapters"
        if not self.manifest_path.is_file():
            raise ValueError(f"找不到 manifest.json：{self.manifest_path}")
        if not self.chapters_dir.is_dir():
            raise ValueError(f"找不到章节目录：{self.chapters_dir}")

    @staticmethod
    def _load_json(path: Path) -> dict[str, Any]:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            raise ValueError(f"JSON 顶层必须是对象：{path}")
        return data

    def _manifest(self) -> dict[str, Any]:
        return self._load_json(self.manifest_path)

    def chapter_path(self, index: int) -> Path:
        return self.chapters_dir / f"ch{index}.json"

    def book_payload(self) -> dict[str, Any]:
        manifest = self._manifest()
        chapters: list[dict[str, Any]] = []
        total_segments = 0
        translated_segments = 0
        latest_path = self.manifest_path

        for item in manifest.get("chapters", []):
            index = int(item.get("index", len(chapters)))
            path = self.chapter_path(index)
            chapter = self._load_json(path)
            segments = [
                segment
                for segment in chapter.get("segments", [])
                if _has_text(segment.get("source"))
            ]
            translated = sum(_has_text(segment.get("target")) for segment in segments)
            total = len(segments)
            total_segments += total
            translated_segments += translated
            if path.stat().st_mtime > latest_path.stat().st_mtime:
                latest_path = path

            stored_status = str(item.get("status", "pending"))
            if stored_status == "done":
                display_status = "done"
            elif translated:
                display_status = "translating"
            else:
                display_status = "pending"

            chapters.append(
                {
                    "index": index,
                    "title": str(item.get("title") or chapter.get("title") or f"章节 {index}"),
                    "status": display_status,
                    "stored_status": stored_status,
                    "review_status": str(item.get("review_status", "pending")),
                    "translated_segments": translated,
                    "total_segments": total,
                    "updated_at": _mtime_iso(path),
                    "revision": path.stat().st_mtime_ns,
                }
            )

        chapter_count = len(chapters)
        done_chapters = sum(chapter["stored_status"] == "done" for chapter in chapters)
        review_done = sum(chapter["review_status"] == "done" for chapter in chapters)
        review_failed = sum(chapter["review_status"] == "failed" for chapter in chapters)
        return {
            "title": str(manifest.get("title", self.run_dir.name)),
            "source_lang": str(manifest.get("source_lang", "")),
            "target_lang": str(manifest.get("target_lang", "")),
            "chapter_count": chapter_count,
            "done_chapters": done_chapters,
            "total_segments": total_segments,
            "translated_segments": translated_segments,
            "translation_complete": chapter_count > 0 and done_chapters == chapter_count,
            "review_done_chapters": review_done,
            "review_failed_chapters": review_failed,
            "review_complete": chapter_count > 0 and review_done == chapter_count,
            "updated_at": _mtime_iso(latest_path),
            "revision": latest_path.stat().st_mtime_ns,
            "chapters": chapters,
        }

    def chapter_payload(self, index: int) -> dict[str, Any]:
        manifest = self._manifest()
        manifest_item = next(
            (
                item
                for item in manifest.get("chapters", [])
                if int(item.get("index", -1)) == index
            ),
            None,
        )
        if manifest_item is None:
            raise KeyError(index)

        path = self.chapter_path(index)
        chapter = self._load_json(path)
        segments = []
        for segment in chapter.get("segments", []):
            if not _has_text(segment.get("source")):
                continue
            target = segment.get("target")
            segments.append(
                {
                    "index": int(segment.get("index", len(segments))),
                    "kind": str(segment.get("kind", "text")),
                    "source": str(segment.get("source", "")),
                    "target": None if target is None else str(target),
                    "translated": _has_text(target),
                }
            )

        return {
            "index": index,
            "title": str(
                manifest_item.get("title")
                or chapter.get("title")
                or f"章节 {index}"
            ),
            "status": str(manifest_item.get("status", "pending")),
            "review_status": str(manifest_item.get("review_status", "pending")),
            "translated_segments": sum(segment["translated"] for segment in segments),
            "total_segments": len(segments),
            "updated_at": _mtime_iso(path),
            "revision": path.stat().st_mtime_ns,
            "segments": segments,
        }


class ReaderHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], state: ReaderState):
        super().__init__(address, ReaderHandler)
        self.reader_state = state


class ReaderHandler(BaseHTTPRequestHandler):
    server: ReaderHTTPServer

    def _send_bytes(self, status: int, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send_bytes(status, "application/json; charset=utf-8", body)

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        path = urlparse(self.path).path
        try:
            if path in {"/", "/index.html"}:
                self._send_bytes(
                    200,
                    "text/html; charset=utf-8",
                    INDEX_PATH.read_bytes(),
                )
                return
            if path == "/api/book":
                self._send_json(200, self.server.reader_state.book_payload())
                return
            match = re.fullmatch(r"/api/chapters/(\d+)", path)
            if match:
                self._send_json(
                    200,
                    self.server.reader_state.chapter_payload(int(match.group(1))),
                )
                return
            if path == "/api/health":
                self._send_json(200, {"ok": True})
                return
            self._send_json(404, {"error": "not_found"})
        except KeyError:
            self._send_json(404, {"error": "chapter_not_found"})
        except (OSError, ValueError, json.JSONDecodeError) as error:
            self._send_json(503, {"error": "state_unavailable", "detail": str(error)})

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[{self.log_date_time_string()}] {fmt % args}")


def detect_lan_ips() -> list[str]:
    addresses: set[str] = set()
    try:
        for address in socket.gethostbyname_ex(socket.gethostname())[2]:
            if not address.startswith("127."):
                addresses.add(address)
    except OSError:
        pass
    return sorted(addresses)


def create_server(state: ReaderState, host: str, port: int) -> ReaderHTTPServer:
    return ReaderHTTPServer((host, port), state)


def main() -> None:
    parser = argparse.ArgumentParser(description="Wenyi 局域网移动阅读器")
    parser.add_argument("run_dir", type=Path, help="state/<书名> 运行状态目录")
    parser.add_argument("--host", default="0.0.0.0", help="监听地址，默认允许局域网访问")
    parser.add_argument("--port", type=int, default=8765, help="监听端口，默认 8765")
    args = parser.parse_args()

    state = ReaderState(args.run_dir)
    server = create_server(state, args.host, args.port)
    print(f"正在阅读：《{state.book_payload()['title']}》")
    print(f"本机访问：http://127.0.0.1:{server.server_port}")
    for address in detect_lan_ips():
        print(f"局域网访问：http://{address}:{server.server_port}")
    print("按 Ctrl+C 停止阅读器。")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n阅读器已停止。")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
