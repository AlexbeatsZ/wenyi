"""通过独立 Chrome 配置逐章发布百度贴吧回复。

正文整理、分层和断点状态不依赖浏览器，便于先预览和离线测试。只有真正发布
时才延迟导入 Playwright，并由用户在专用浏览器窗口中完成一次登录。
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import re
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from ..ingest.models import Chapter
from ..pipeline.runstore import STATUS_DONE, RunStore

_THREAD_ID_RE = re.compile(r"^https?://tieba\.baidu\.com/p/(\d+)(?:[/?#].*)?$")
_EDITOR_SELECTOR = '#tb-editor-pb-content .ql-editor[contenteditable="true"]'
_PUBLISH_SELECTOR = ".publish-btn"
_SECURITY_TEXTS = ("验证码", "安全验证", "操作过于频繁", "发布失败")
_RETRY_DELAY_SECONDS = 30


class TiebaPublishError(RuntimeError):
    """贴吧发布准备、提交或确认失败。"""


class PublishRunLock:
    """用操作系统文件锁阻止同一断点被多个发布进程同时使用。"""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.handle: Any = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("a+b")
        self.handle.seek(0, os.SEEK_END)
        if self.handle.tell() == 0:
            self.handle.write(b"\0")
            self.handle.flush()
        self.handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:  # pragma: no cover - Windows 是本项目当前运行环境
                import fcntl

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            self.handle.close()
            self.handle = None
            raise TiebaPublishError(
                "已有另一个贴吧发布进程正在使用同一断点；请勿重复启动"
            ) from error
        return self

    def __exit__(self, *_: object) -> None:
        if self.handle is None:
            return
        try:
            self.handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:  # pragma: no cover - Windows 是本项目当前运行环境
                import fcntl

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        finally:
            self.handle.close()
            self.handle = None


@dataclass(frozen=True)
class PublishPart:
    """一层待发布回复。"""

    chapter: int
    part: int
    part_count: int
    marker: str
    body: str

    @property
    def key(self) -> str:
        """返回断点文件中的稳定键。"""
        return f"ch{self.chapter}-part{self.part}"

    @property
    def body_hash(self) -> str:
        """返回正文摘要，用于阻止译文变化后的无意重复发布。"""
        return hashlib.sha256(self.body.encode("utf-8")).hexdigest()


def thread_id_from_url(url: str) -> str:
    """校验贴吧主题 URL 并返回主题 ID。"""
    match = _THREAD_ID_RE.match(url.strip())
    if not match:
        raise ValueError("只支持 https://tieba.baidu.com/p/<主题ID> 形式的贴吧链接")
    return match.group(1)


def chapter_paragraphs(chapter: Chapter) -> list[str]:
    """把章节译文还原为适合贴吧的段落，跳过 EPUB 前置日期/字数元数据。"""
    nonempty = [
        segment
        for segment in chapter.segments
        if isinstance(segment.target, str) and segment.target.strip()
    ]
    if not nonempty:
        raise ValueError(f"第 {chapter.index} 章没有可发布的译文")

    first_heading = next(
        (index for index, segment in enumerate(nonempty) if segment.kind == "heading"),
        0,
    )
    paragraphs: list[str] = []
    for segment in nonempty[first_heading:]:
        text = segment.target.strip()
        if segment.cont and paragraphs:
            paragraphs[-1] += text
        else:
            paragraphs.append(text)
    return paragraphs


def _split_paragraphs(paragraphs: Iterable[str], limit: int) -> list[list[str]]:
    """在不超过字符预算的前提下优先按段落切分，并平衡过短的末层。"""
    if limit < 200:
        raise ValueError("单层字符上限不能小于 200")

    chunks: list[list[str]] = []
    current: list[str] = []
    current_len = 0
    for paragraph in paragraphs:
        pending = paragraph
        while len(pending) > limit:
            if current:
                chunks.append(current)
                current = []
                current_len = 0
            chunks.append([pending[:limit]])
            pending = pending[limit:]
        if not pending:
            continue
        extra = len(pending) + (2 if current else 0)
        if current and current_len + extra > limit:
            chunks.append(current)
            current = []
            current_len = 0
        current.append(pending)
        current_len += len(pending) + (2 if current_len else 0)
    if current:
        chunks.append(current)

    def chunk_length(chunk: list[str]) -> int:
        return sum(len(paragraph) for paragraph in chunk) + max(0, len(chunk) - 1) * 2

    for index in range(len(chunks) - 1, 0, -1):
        while (
            chunk_length(chunks[index]) < limit * 0.4
            and len(chunks[index - 1]) > 1
        ):
            candidate = chunks[index - 1][-1]
            balanced = [candidate, *chunks[index]]
            if chunk_length(balanced) > limit:
                break
            chunks[index - 1].pop()
            chunks[index] = balanced
    return chunks


def build_chapter_parts(chapter: Chapter, *, max_chars: int = 1950) -> list[PublishPart]:
    """把一个逻辑章变成一层或多层带唯一标记的回复。"""
    marker_budget = len(f"【第{chapter.index}话（999/999）】\n\n")
    chunks = _split_paragraphs(
        chapter_paragraphs(chapter),
        max_chars - marker_budget,
    )
    part_count = len(chunks)
    parts: list[PublishPart] = []
    for part_number, chunk in enumerate(chunks, 1):
        if part_count == 1:
            marker = f"【第{chapter.index}话】"
        else:
            marker = f"【第{chapter.index}话（{part_number}/{part_count}）】"
        body = marker + "\n\n" + "\n\n".join(chunk)
        if len(body) > max_chars:
            raise AssertionError("贴吧回复分层超过字符上限")
        parts.append(
            PublishPart(
                chapter=chapter.index,
                part=part_number,
                part_count=part_count,
                marker=marker,
                body=body,
            )
        )
    return parts


def build_publish_plan(
    store: RunStore,
    *,
    start: int = 1,
    end: int | None = None,
    max_chars: int = 1950,
) -> list[PublishPart]:
    """从翻译状态构建一个闭区间发布计划。"""
    manifest = store.load_manifest()
    chapters = manifest.get("chapters", [])
    if not chapters:
        raise ValueError("翻译状态中没有章节")
    final = max(int(item["index"]) for item in chapters) if end is None else end
    if start < 0 or final < start:
        raise ValueError("章节范围无效")

    plan: list[PublishPart] = []
    selected = [
        item
        for item in chapters
        if start <= int(item["index"]) <= final
    ]
    if not selected:
        raise ValueError("指定范围内没有章节")
    for item in selected:
        chapter_index = int(item["index"])
        if item.get("status") != STATUS_DONE:
            raise ValueError(f"第 {chapter_index} 章尚未完成翻译")
        plan.extend(
            build_chapter_parts(
                store.load_chapter(chapter_index),
                max_chars=max_chars,
            )
        )
    return plan


def rendered_body_matches(expected: str, actual: str) -> tuple[bool, int]:
    """比较贴吧渲染正文，并接受服务器做出的等长星号过滤。"""
    normalized_expected = re.sub(r"\s+", "", expected)
    normalized_actual = re.sub(r"\s+", "", actual)
    if len(normalized_expected) != len(normalized_actual):
        return False, 0
    differences = [
        index
        for index, (expected_char, actual_char) in enumerate(
            zip(normalized_expected, normalized_actual, strict=True)
        )
        if expected_char != actual_char
    ]
    if not differences:
        return True, 0
    redacted = all(
        normalized_actual[index] == "*" and normalized_expected[index] != "*"
        for index in differences
    )
    return redacted, len(differences) if redacted else 0


class PublishJournal:
    """逐层原子记录提交前与提交后的状态。"""

    def __init__(self, path: str | Path, *, thread_url: str):
        self.path = Path(path)
        self.thread_url = thread_url
        self.thread_id = thread_id_from_url(thread_url)
        self.data = self._load()

    def _load(self) -> dict[str, Any]:
        if not self.path.is_file():
            return {
                "version": 1,
                "thread_id": self.thread_id,
                "thread_url": self.thread_url,
                "items": {},
            }
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if str(data.get("thread_id")) != self.thread_id:
            raise TiebaPublishError("断点文件属于另一个贴吧主题")
        if not isinstance(data.get("items"), dict):
            raise TiebaPublishError("贴吧发布断点文件格式无效")
        return data

    def status(self, part: PublishPart) -> str | None:
        """返回本层状态，并在正文已变化时拒绝静默复用旧断点。"""
        item = self.data["items"].get(part.key)
        if not item:
            return None
        if item.get("body_hash") != part.body_hash:
            raise TiebaPublishError(
                f"{part.key} 的译文在记录后发生变化；请人工核对已发内容和断点文件"
            )
        return str(item.get("status"))

    def mark(self, part: PublishPart, status: str, **extra: Any) -> None:
        """写入一层状态；提交前的 submitting 可避免崩溃后自动重复发帖。"""
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        item = {
            "chapter": part.chapter,
            "part": part.part,
            "part_count": part.part_count,
            "marker": part.marker,
            "body_hash": part.body_hash,
            "status": status,
            "updated_at": now,
            **extra,
        }
        self.data["items"][part.key] = item
        self._save()

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temporary, self.path)


def default_profile_dir() -> Path:
    """返回项目内持久、且不会提交到 Git 的专用 Chrome 配置目录。"""
    return Path.cwd() / "state" / "publish" / "tieba-chrome-profile"


class TiebaBrowserPublisher:
    """在专用的可见 Chrome 窗口中填写并提交回复。"""

    def __init__(
        self,
        *,
        profile_dir: str | Path,
        prompt: Callable[[str], object] = print,
    ):
        self.profile_dir = Path(profile_dir)
        self.prompt = prompt
        self._playwright: Any = None
        self._browser_error: type[Exception] = RuntimeError
        self._browser_timeout: type[Exception] = TimeoutError
        self.context: Any = None
        self.page: Any = None
        self.thread_url: str | None = None

    def __enter__(self):
        try:
            import playwright.sync_api as playwright_api
        except ImportError as error:  # pragma: no cover - dependency normally installed
            raise TiebaPublishError("缺少 Playwright；请先运行 uv sync") from error
        self._browser_error = playwright_api.Error
        self._browser_timeout = playwright_api.TimeoutError
        try:
            self.profile_dir.mkdir(parents=True, exist_ok=True)
            self._playwright = playwright_api.sync_playwright().start()
            self.context = self._playwright.chromium.launch_persistent_context(
                user_data_dir=str(self.profile_dir),
                channel="chrome",
                headless=False,
                viewport=None,
                args=["--hide-crash-restore-bubble"],
            )
            restored_pages = list(self.context.pages)
            self.page = self.context.new_page()
            for restored_page in restored_pages:
                restored_page.close()
            self.page.set_default_timeout(15_000)
        except playwright_api.Error as error:
            if self._playwright is not None:
                self._playwright.stop()
            raise TiebaPublishError(f"无法启动专用 Chrome：{error}") from error
        return self

    def __exit__(self, *_: object) -> None:
        try:
            if self.context is not None:
                self.context.close()
        except self._browser_error:
            pass
        finally:
            if self._playwright is not None:
                self._playwright.stop()

    def open_thread(self, thread_url: str) -> None:
        """打开目标主题；专用配置未登录时在可见窗口等待用户完成登录。"""
        self.thread_url = thread_url
        try:
            self._goto_thread(thread_url)
            if self._open_editor():
                return
            login_page = self.context.new_page()
            login_page.goto("https://tieba.baidu.com/", wait_until="domcontentloaded")
            login_page.bring_to_front()
            self.prompt(
                "目标主题在未登录的专用 Chrome 中可能显示 404。"
                "请在已经打开的贴吧首页标签页登录；"
                "脚本会等待最多 10 分钟并自动继续。"
            )
            deadline = time.monotonic() + 600
            while time.monotonic() < deadline:
                self._goto_thread(thread_url)
                if self._open_editor():
                    self.page.bring_to_front()
                    login_page.close()
                    return
                time.sleep(10)
            raise TiebaPublishError("等待登录超时，请确认账号可在该主题回复")
        except self._browser_error as error:
            raise TiebaPublishError(f"无法打开贴吧主题：{error}") from error

    def _goto_thread(self, thread_url: str) -> None:
        """导航到主题，并容忍登录完成瞬间由贴吧触发的同页跳转。"""
        for attempt in range(3):
            try:
                self.page.goto(thread_url, wait_until="domcontentloaded")
                return
            except self._browser_error as error:
                transient = "interrupted by another navigation" in str(error)
                if not transient or attempt == 2:
                    raise
                self.page.wait_for_timeout(1_000)

    def _open_editor(self) -> bool:
        reply_box = self.page.locator(".pc-pb-reply-box")
        try:
            reply_box.wait_for(state="visible", timeout=15_000)
        except self._browser_timeout:
            return False
        editor = self.page.locator(_EDITOR_SELECTOR)
        if editor.count() == 1 and editor.is_visible():
            return True
        collapsed = self.page.locator(".pc-pb-reply-box .reply-input")
        if collapsed.count() == 1:
            try:
                collapsed.click(timeout=2_000)
            except self._browser_timeout:
                return False
        editor = self.page.locator(_EDITOR_SELECTOR)
        try:
            editor.wait_for(state="visible", timeout=5_000)
        except self._browser_timeout:
            return False
        return editor.count() == 1

    def _ensure_editor(self) -> None:
        """回复框偶发未渲染时刷新主题重试，期间尚未提交所以不会重复发帖。"""
        for attempt in range(3):
            if self._open_editor():
                return
            if self.thread_url is None or attempt == 2:
                break
            self._goto_thread(self.thread_url)
            self.page.wait_for_timeout(2_000)
        raise TiebaPublishError("贴吧回复框刷新重试后仍不可用，登录可能已失效")

    def post(self, part: PublishPart) -> int:
        """提交一层，并用倒序刷新页确认服务器已持久保存；失败时仅重试一次。"""
        try:
            for attempt in range(2):
                self._post(part)
                self.page.wait_for_timeout(3_000)
                redacted_chars = self.inspect_post(part)
                if redacted_chars is not None:
                    return redacted_chars
                if attempt == 0:
                    self.prompt(
                        f"{part.marker} 刷新倒序页后仍未出现；"
                        f"等待 {_RETRY_DELAY_SECONDS} 秒复核，确认缺失后只重试一次。"
                    )
                    self.page.wait_for_timeout(_RETRY_DELAY_SECONDS * 1_000)
                    redacted_chars = self.inspect_post(part)
                    if redacted_chars is not None:
                        return redacted_chars
                    if self.thread_url is None:
                        raise TiebaPublishError("尚未打开目标主题")
                    self._goto_thread(self.thread_url)
            raise TiebaPublishError(
                f"{part.marker} 两次提交后仍未在倒序页找到；"
                "已保留为 submitting，请人工核对"
            )
        except self._browser_error as error:
            raise TiebaPublishError(f"{part.marker} 浏览器操作失败：{error}") from error

    def _post(self, part: PublishPart) -> None:
        """执行一次浏览器提交；Playwright 错误由 ``post`` 统一转换。"""
        self._ensure_editor()
        editor = self.page.locator(_EDITOR_SELECTOR)
        # Quill 会把传入的每个换行渲染为一个段落分隔；若直接传入双换行，
        # 读取时会扩成四个换行，并可能让实际字数越过贴吧上限。
        editor.fill(part.body.replace("\n\n", "\n"))
        rendered = editor.inner_text()
        if rendered != part.body:
            raise TiebaPublishError(
                f"{part.marker} 填入编辑器后的正文不一致，已停止发布"
            )
        publish = self.page.locator(_PUBLISH_SELECTOR).filter(has_text="发布")
        if publish.count() != 1:
            raise TiebaPublishError("无法唯一定位贴吧发布按钮")
        classes = publish.get_attribute("class") or ""
        if "disabled" in classes:
            raise TiebaPublishError("贴吧发布按钮仍为禁用状态")
        publish_center = publish.locator(".center")
        if publish_center.count() != 1:
            raise TiebaPublishError("无法定位贴吧发布按钮的可点击区域")
        publish_center.click()

        deadline = time.monotonic() + 30
        notified_security: set[str] = set()
        while time.monotonic() < deadline:
            if editor.count() == 0:
                return
            try:
                if not editor.inner_text().strip():
                    return
            except self._browser_error:
                return
            page_text = self.page.locator("body").inner_text(timeout=2_000)
            matched = next((text for text in _SECURITY_TEXTS if text in page_text), None)
            if matched and matched not in notified_security:
                notified_security.add(matched)
                self.prompt(
                    f"贴吧显示“{matched}”。请在浏览器中按页面要求处理，"
                    "脚本会等待最多 10 分钟，不会尝试绕过验证。"
                )
                deadline = time.monotonic() + 600
            time.sleep(1)
        raise TiebaPublishError(
            f"{part.marker} 提交后未确认成功；已保留为 submitting，"
            "请先在主题中人工核对，避免重复发布"
        )

    def inspect_post(self, part: PublishPart) -> int | None:
        """刷新并切到倒序，只以服务器重新加载出的最新楼层作为成功证据。"""
        self._open_reverse_view()

        floor = self.page.locator(".pb-text-wrapper").filter(has_text=part.marker)
        count = floor.count()
        if count > 1:
            raise TiebaPublishError(
                f"{part.marker} 在倒序页出现 {count} 次，疑似重复发布"
            )
        if count == 0:
            return None
        matches, redacted_chars = rendered_body_matches(
            part.body,
            floor.inner_text(),
        )
        if not matches:
            raise TiebaPublishError(
                f"{part.marker} 已持久保存，但正文存在非星号过滤差异"
            )
        return redacted_chars

    def _open_reverse_view(self) -> None:
        """刷新主题并切换到倒序，使最新持久楼层出现在虚拟列表顶部。"""
        reason = "贴吧回复列表刷新后不可用"
        for attempt in range(5):
            if attempt == 0:
                self.page.reload(wait_until="domcontentloaded")
            elif self.thread_url is not None:
                self._goto_thread(self.thread_url)
            else:
                self.page.reload(wait_until="domcontentloaded")
            reply_list = self.page.locator(".pc-pb-box")
            try:
                reply_list.wait_for(state="visible", timeout=15_000)
            except self._browser_timeout:
                reason = "贴吧回复列表未渲染"
                self.page.wait_for_timeout((attempt + 1) * 2_000)
                continue

            reverse = self.page.locator(".sub-tab-item").filter(has_text="倒序")
            if reverse.count() != 1:
                reason = f"倒序查看按钮数量为 {reverse.count()}"
                self.page.wait_for_timeout((attempt + 1) * 2_000)
                continue
            classes = reverse.get_attribute("class") or ""
            if "sub-tab-item-active" not in classes:
                reverse.click()
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                classes = reverse.get_attribute("class") or ""
                if "sub-tab-item-active" in classes:
                    self.page.wait_for_timeout(2_000)
                    return
                time.sleep(0.5)
            reason = "贴吧未能切换到倒序查看"
        raise TiebaPublishError(f"{reason}；连续 5 次恢复失败")

def journal_path_for(store: RunStore, thread_url: str) -> Path:
    """返回本书与本主题组合对应的断点文件路径。"""
    return Path(store.run_dir) / "publish" / f"tieba-{thread_id_from_url(thread_url)}.json"


def publish_plan(
    plan: list[PublishPart],
    *,
    thread_url: str,
    journal: PublishJournal,
    profile_dir: str | Path,
    delay_seconds: float,
    jitter_seconds: float,
    prompt: Callable[[str], object] = print,
    progress: Callable[[str], None] = print,
) -> None:
    """单实例按顺序发布，并自动对账上次中断在 submitting 的最新层。"""
    lock_path = journal.path.with_suffix(journal.path.suffix + ".lock")
    with PublishRunLock(lock_path):
        pending = [part for part in plan if journal.status(part) != "posted"]
        if not pending:
            progress("所选范围已经全部发布，无需重复操作。")
            return

        try:
            with TiebaBrowserPublisher(
                profile_dir=profile_dir,
                prompt=prompt,
            ) as publisher:
                publisher.open_thread(thread_url)
                for position, part in enumerate(pending, 1):
                    status = journal.status(part)
                    if status == "submitting":
                        progress(f"自动对账上次中断层 {part.marker}…")
                        redacted_chars = publisher.inspect_post(part)
                        if redacted_chars is not None:
                            journal.mark(
                                part,
                                "posted",
                                redacted_chars=redacted_chars,
                                recovered_from="submitting",
                            )
                            suffix = (
                                f"，贴吧过滤 {redacted_chars} 字符"
                                if redacted_chars
                                else ""
                            )
                            progress(
                                f"已恢复 {part.marker}：倒序页正文完整{suffix}"
                            )
                            continue
                        journal.mark(
                            part,
                            "pending",
                            error="倒序刷新页确认不存在，允许安全重试",
                        )
                        progress(f"{part.marker} 未持久保存，将安全重试")

                    progress(
                        f"[{position}/{len(pending)}] 发布 {part.marker}，"
                        f"{len(part.body)} 字"
                    )
                    journal.mark(part, "submitting")
                    try:
                        redacted_chars = publisher.post(part)
                    except (OSError, TiebaPublishError) as error:
                        journal.mark(part, "submitting", error=str(error))
                        raise
                    journal.mark(
                        part,
                        "posted",
                        redacted_chars=redacted_chars,
                    )
                    suffix = (
                        f"；贴吧过滤 {redacted_chars} 字符"
                        if redacted_chars
                        else ""
                    )
                    progress(
                        f"已核对 {part.marker}："
                        f"倒序刷新页楼层唯一且正文完整{suffix}"
                    )
                    if position < len(pending):
                        wait = max(0.0, delay_seconds) + random.uniform(
                            0.0, max(0.0, jitter_seconds)
                        )
                        progress(f"等待 {wait:.0f} 秒后发布下一层…")
                        time.sleep(wait)
        except TiebaPublishError:
            raise
        except (OSError, RuntimeError) as error:
            raise TiebaPublishError(f"浏览器操作失败：{error}") from error
