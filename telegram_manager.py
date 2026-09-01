# -*- coding: utf-8 -*-

"""Telegram button manager for URL_config.ini."""

from __future__ import annotations

import datetime
import hashlib
import json
import math
import os
import re
import shutil
import tempfile
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from supported_urls import is_supported_url


QUALITIES = ("原画", "蓝光", "超清", "高清", "标清", "流畅")
PAGE_SIZE = 8
PENDING_TIMEOUT_SECONDS = 600
URL_PATTERN = re.compile(r"https?://[^\s,，]+", re.IGNORECASE)


@dataclass(frozen=True)
class StreamEntry:
    line_index: int
    paused: bool
    quality: str
    explicit_quality: bool
    url: str
    name: str

    @property
    def display_name(self) -> str:
        name = self.name.strip()
        if name.startswith("主播:"):
            name = name.split("主播:", 1)[1].strip()
        if name:
            return name
        tail = self.url.rstrip("/").rsplit("/", 1)[-1]
        return tail[:30] or self.url[:30]


class URLConfig:
    def __init__(self, path: str, backup_dir: str, lock: threading.Lock | None = None):
        self.path = Path(path)
        self.backup_dir = Path(backup_dir)
        self.lock = lock or threading.Lock()

    @staticmethod
    def _revision(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]

    @staticmethod
    def _parse_line(raw_line: str, line_index: int) -> StreamEntry | None:
        line = raw_line.strip()
        if not line:
            return None
        paused = line.startswith("#")
        if paused:
            line = line.lstrip("#").strip()

        parts = [part.strip() for part in re.split(r"[,，]", line, maxsplit=2)]
        explicit_quality = bool(parts and parts[0] in QUALITIES)
        if explicit_quality:
            if len(parts) < 2:
                return None
            quality, url = parts[0], parts[1]
            name = parts[2] if len(parts) > 2 else ""
        else:
            quality = "默认"
            url = parts[0]
            name = parts[1] if len(parts) > 1 else ""

        if not URL_PATTERN.fullmatch(url) or not is_supported_url(url):
            return None
        return StreamEntry(line_index, paused, quality, explicit_quality, url, name)

    @staticmethod
    def _normal_url(url: str) -> str:
        return url.strip().rstrip("/")

    def _read_unlocked(self) -> tuple[str, list[str], list[StreamEntry]]:
        text = self.path.read_text(encoding="utf-8-sig", errors="ignore") if self.path.exists() else ""
        lines = text.splitlines(keepends=True)
        entries = []
        for index, line in enumerate(lines):
            entry = self._parse_line(line, index)
            if entry:
                entries.append(entry)
        return text, lines, entries

    def snapshot(self) -> tuple[str, list[StreamEntry]]:
        with self.lock:
            text, _, entries = self._read_unlocked()
            return self._revision(text), entries

    def parse_additions(self, text: str) -> tuple[list[str], list[str]]:
        valid = []
        invalid = []
        for raw in text.splitlines():
            raw = raw.strip()
            if not raw:
                continue
            entry = self._parse_line(raw, 0)
            if not entry:
                invalid.append(raw)
                continue
            line = f"{entry.quality},{entry.url}" if entry.explicit_quality else entry.url
            if entry.name:
                line += f",{entry.name}"
            valid.append(line)
        return valid[:50], invalid + valid[50:]

    def _backup_unlocked(self) -> None:
        if not self.path.exists():
            return
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S_%f")
        shutil.copy2(self.path, self.backup_dir / f"URL_config.ini_{stamp}")

    def _write_unlocked(self, lines: list[str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        file_descriptor, temp_path = tempfile.mkstemp(prefix=".URL_config.", dir=self.path.parent)
        try:
            with os.fdopen(file_descriptor, "w", encoding="utf-8-sig", newline="") as file:
                file.write("".join(lines))
                file.flush()
                os.fsync(file.fileno())
            if self.path.exists():
                os.chmod(temp_path, self.path.stat().st_mode)
            os.replace(temp_path, self.path)
        finally:
            if os.path.exists(temp_path):
                os.unlink(temp_path)

    @staticmethod
    def _replace_line(lines: list[str], index: int, value: str) -> None:
        newline = "\n" if lines[index].endswith(("\n", "\r")) else ""
        lines[index] = value + newline

    def _entry_for_change(self, expected_revision: str, line_index: int):
        text, lines, entries = self._read_unlocked()
        if self._revision(text) != expected_revision:
            raise ValueError("列表已经变化，请刷新后重试")
        entry = next((item for item in entries if item.line_index == line_index), None)
        if entry is None:
            raise ValueError("主播已经不存在，请刷新列表")
        return lines, entry

    def add(self, additions: list[str]) -> tuple[int, int]:
        with self.lock:
            _, lines, entries = self._read_unlocked()
            existing = {self._normal_url(item.url) for item in entries}
            new_lines = []
            duplicate_count = 0
            for raw in additions:
                entry = self._parse_line(raw, 0)
                if not entry or self._normal_url(entry.url) in existing:
                    duplicate_count += 1
                    continue
                existing.add(self._normal_url(entry.url))
                new_lines.append(raw)
            if not new_lines:
                return 0, duplicate_count
            if lines and not lines[-1].endswith(("\n", "\r")):
                lines[-1] += "\n"
            lines.extend(f"{line}\n" for line in new_lines)
            self._backup_unlocked()
            self._write_unlocked(lines)
            return len(new_lines), duplicate_count

    def toggle(self, expected_revision: str, line_index: int) -> bool:
        with self.lock:
            lines, entry = self._entry_for_change(expected_revision, line_index)
            raw = lines[line_index].rstrip("\r\n").strip()
            replacement = raw.lstrip("#").strip() if entry.paused else f"#{raw}"
            self._replace_line(lines, line_index, replacement)
            self._backup_unlocked()
            self._write_unlocked(lines)
            return not entry.paused

    def update_url(self, expected_revision: str, line_index: int, new_url: str) -> None:
        if not URL_PATTERN.fullmatch(new_url.strip()) or not is_supported_url(new_url):
            raise ValueError("请输入受支持平台的直播间链接，或 .flv/.m3u8 直播直链")
        with self.lock:
            lines, entry = self._entry_for_change(expected_revision, line_index)
            value = f"{entry.quality},{new_url.strip()}" if entry.explicit_quality else new_url.strip()
            if entry.paused:
                value = f"#{value}"
            self._replace_line(lines, line_index, value)
            self._backup_unlocked()
            self._write_unlocked(lines)

    def update_quality(self, expected_revision: str, line_index: int, quality: str) -> None:
        if quality not in QUALITIES and quality != "默认":
            raise ValueError("不支持的画质")
        with self.lock:
            lines, entry = self._entry_for_change(expected_revision, line_index)
            value = entry.url if quality == "默认" else f"{quality},{entry.url}"
            if entry.name:
                value += f",{entry.name}"
            if entry.paused:
                value = f"#{value}"
            self._replace_line(lines, line_index, value)
            self._backup_unlocked()
            self._write_unlocked(lines)

    def delete(self, expected_revision: str, line_index: int) -> None:
        with self.lock:
            lines, _ = self._entry_for_change(expected_revision, line_index)
            del lines[line_index]
            self._backup_unlocked()
            self._write_unlocked(lines)


class TelegramManager(threading.Thread):
    def __init__(
        self,
        token: str,
        chat_ids: set[int],
        admin_user_ids: set[int],
        url_config: URLConfig,
        recording_provider: Callable[[], list[str]],
    ):
        super().__init__(name="telegram-manager", daemon=True)
        self.api_url = f"https://api.telegram.org/bot{token}/"
        self.chat_ids = chat_ids
        self.admin_user_ids = admin_user_ids
        self.url_config = url_config
        self.recording_provider = recording_provider
        self.pending: dict[int, dict] = {}
        self.offset: int | None = None

    def _set_pending(self, user_id: int, **values):
        values["expires_at"] = time.monotonic() + PENDING_TIMEOUT_SECONDS
        self.pending[user_id] = values

    def _get_pending(self, user_id: int) -> dict | None:
        pending = self.pending.get(user_id)
        if pending and pending.get("expires_at", 0) > time.monotonic():
            return pending
        self.pending.pop(user_id, None)
        return None

    @staticmethod
    def parse_ids(value: str) -> set[int]:
        result = set()
        for item in value.replace("，", ",").split(","):
            try:
                if item.strip():
                    result.add(int(item.strip()))
            except ValueError:
                continue
        return result

    def _api(self, method: str, payload: dict, timeout: int = 20):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            self.api_url + method,
            data=data,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
        if not body.get("ok"):
            raise RuntimeError(body.get("description", "Telegram API 请求失败"))
        return body.get("result")

    def _configure_commands(self):
        commands = [
            {"command": "manage", "description": "打开主播管理"},
            {"command": "list", "description": "查看主播列表"},
            {"command": "recording", "description": "查看正在录制"},
            {"command": "cancel", "description": "取消当前操作"},
        ]
        self._api("setMyCommands", {"commands": commands})
        for chat_id in self.chat_ids:
            if chat_id > 0:
                self._api("setChatMenuButton", {"chat_id": chat_id, "menu_button": {"type": "commands"}})

    @staticmethod
    def _keyboard(rows: list[list[tuple[str, str]]]) -> dict:
        return {
            "inline_keyboard": [
                [{"text": text, "callback_data": data} for text, data in row]
                for row in rows
            ]
        }

    def _send(self, chat_id: int, text: str, rows=None):
        payload = {"chat_id": chat_id, "text": text, "disable_web_page_preview": True}
        if rows:
            payload["reply_markup"] = self._keyboard(rows)
        return self._api("sendMessage", payload)

    def _edit(self, chat_id: int, message_id: int, text: str, rows=None):
        payload = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "disable_web_page_preview": True,
        }
        if rows:
            payload["reply_markup"] = self._keyboard(rows)
        try:
            return self._api("editMessageText", payload)
        except Exception:
            return self._send(chat_id, text, rows)

    def _authorized(self, chat_id: int, user_id: int) -> bool:
        chat_allowed = not self.chat_ids or chat_id in self.chat_ids
        user_allowed = user_id in self.admin_user_ids
        return chat_allowed and user_allowed

    def _menu(self, chat_id: int, message_id: int | None = None):
        _, entries = self.url_config.snapshot()
        active = sum(not item.paused for item in entries)
        paused = len(entries) - active
        recording = len(self.recording_provider())
        text = f"📺 录制管理\n\n启用：{active}  暂停：{paused}  正在录制：{recording}"
        rows = [
            [("📋 主播列表", "list:0"), ("🔴 正在录制", "recording")],
            [("➕ 添加主播", "add"), ("🔄 刷新", "menu")],
        ]
        return self._edit(chat_id, message_id, text, rows) if message_id else self._send(chat_id, text, rows)

    def _list(self, chat_id: int, page: int, message_id: int | None = None):
        revision, entries = self.url_config.snapshot()
        total_pages = max(1, math.ceil(len(entries) / PAGE_SIZE))
        page = max(0, min(page, total_pages - 1))
        visible = entries[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]
        text = f"📋 主播列表  {page + 1}/{total_pages}\n共 {len(entries)} 个，点击主播查看操作"
        rows = []
        for entry in visible:
            state = "⏸" if entry.paused else "✅"
            title = f"{state} {entry.display_name}｜{entry.quality}"[:55]
            rows.append([(title, f"show:{revision}:{entry.line_index}:{page}")])
        navigation = []
        if page > 0:
            navigation.append(("⬅️ 上一页", f"list:{page - 1}"))
        if page + 1 < total_pages:
            navigation.append(("下一页 ➡️", f"list:{page + 1}"))
        if navigation:
            rows.append(navigation)
        rows.append([("➕ 添加", "add"), ("🏠 主页", "menu")])
        return self._edit(chat_id, message_id, text, rows) if message_id else self._send(chat_id, text, rows)

    def _find_entry(self, revision: str, line_index: int) -> StreamEntry:
        current_revision, entries = self.url_config.snapshot()
        if current_revision != revision:
            raise ValueError("列表已经变化，请刷新后重试")
        entry = next((item for item in entries if item.line_index == line_index), None)
        if entry is None:
            raise ValueError("主播已经不存在，请刷新列表")
        return entry

    def _detail(self, chat_id: int, message_id: int, revision: str, line_index: int, page: int):
        entry = self._find_entry(revision, line_index)
        state = "已暂停" if entry.paused else "已启用"
        text = (
            f"📺 {entry.display_name}\n\n状态：{state}\n画质：{entry.quality}"
            f"\n链接：{entry.url}"
        )
        toggle_text = "▶️ 恢复" if entry.paused else "⏸ 暂停"
        rows = [
            [(toggle_text, f"toggle:{revision}:{line_index}:{page}")],
            [("🔗 修改链接", f"edit:{revision}:{line_index}:{page}"),
             ("🎞 修改画质", f"qmenu:{revision}:{line_index}:{page}")],
            [("🗑 删除", f"delete:{revision}:{line_index}:{page}")],
            [("⬅️ 返回列表", f"list:{page}")],
        ]
        self._edit(chat_id, message_id, text, rows)

    def _recording(self, chat_id: int, message_id: int | None = None):
        names = self.recording_provider()
        if names:
            text = f"🔴 正在录制 {len(names)} 个\n\n" + "\n".join(f"• {name[:80]}" for name in names[:30])
        else:
            text = "当前没有正在录制的直播"
        rows = [[("🔄 刷新", "recording"), ("🏠 主页", "menu")]]
        return self._edit(chat_id, message_id, text, rows) if message_id else self._send(chat_id, text, rows)

    def _handle_message(self, message: dict):
        chat_id = int(message["chat"]["id"])
        user_id = int(message.get("from", {}).get("id", 0))
        text = message.get("text", "").strip()
        if not self._authorized(chat_id, user_id):
            self._send(chat_id, f"无权操作。你的用户 ID：{user_id}")
            return

        command = text.split(maxsplit=1)[0].split("@", 1)[0].lower() if text.startswith("/") else ""
        if command in ("/start", "/manage"):
            self.pending.pop(user_id, None)
            self._menu(chat_id)
            return
        if command == "/list":
            self.pending.pop(user_id, None)
            self._list(chat_id, 0)
            return
        if command == "/recording":
            self.pending.pop(user_id, None)
            self._recording(chat_id)
            return
        if command == "/cancel":
            self.pending.pop(user_id, None)
            self._send(chat_id, "已取消", [[("🏠 返回主页", "menu")]])
            return

        pending = self._get_pending(user_id)
        if not pending:
            self._send(chat_id, "当前没有待处理操作或操作已超时，请使用 /manage 打开管理菜单")
            return
        if pending["action"] == "add_input":
            valid, invalid = self.url_config.parse_additions(text)
            if not valid:
                self._send(chat_id, "没有识别到受支持的直播链接，请发送项目支持的平台地址或 .flv/.m3u8 直链，也可使用 /cancel")
                return
            self._set_pending(user_id, action="add_confirm", items=valid)
            preview = "\n".join(f"• {line}" for line in valid[:10])
            if len(valid) > 10:
                preview += f"\n…其余 {len(valid) - 10} 条"
            summary = f"准备添加 {len(valid)} 条"
            if invalid:
                summary += f"，另有 {len(invalid)} 条格式或平台不支持"
            self._send(
                chat_id,
                f"{summary}\n\n{preview}",
                [[("✅ 确认添加", "add_yes"), ("取消", "add_no")]],
            )
            return
        if pending["action"] == "edit_link":
            if not URL_PATTERN.fullmatch(text) or not is_supported_url(text):
                self._send(chat_id, "请输入受支持平台的直播间链接或 .flv/.m3u8 直播直链，也可使用 /cancel")
                return
            try:
                self.url_config.update_url(pending["revision"], pending["line_index"], text)
                self.pending.pop(user_id, None)
                self._send(chat_id, "✅ 链接已修改，主程序会自动重新读取", [[("📋 返回列表", "list:0")]])
            except ValueError as error:
                self.pending.pop(user_id, None)
                self._send(chat_id, f"❌ {error}", [[("📋 刷新列表", "list:0")]])

    def _handle_callback(self, query: dict):
        callback_id = query["id"]
        user_id = int(query.get("from", {}).get("id", 0))
        message = query.get("message", {})
        chat_id = int(message.get("chat", {}).get("id", 0))
        message_id = int(message.get("message_id", 0))
        if not self._authorized(chat_id, user_id):
            self._api("answerCallbackQuery", {"callback_query_id": callback_id, "text": "无权操作", "show_alert": True})
            return
        self._api("answerCallbackQuery", {"callback_query_id": callback_id})
        data = query.get("data", "")
        parts = data.split(":")
        action = parts[0]
        if action not in ("add_yes", "add_no"):
            self.pending.pop(user_id, None)
        try:
            if action == "menu":
                self._menu(chat_id, message_id)
            elif action == "list":
                self._list(chat_id, int(parts[1]), message_id)
            elif action == "recording":
                self._recording(chat_id, message_id)
            elif action == "add":
                self._set_pending(user_id, action="add_input")
                self._edit(chat_id, message_id, "请发送一条或多条直播链接，每行一条。\n可使用：超清,链接\n发送 /cancel 取消")
            elif action == "add_yes":
                pending = self._get_pending(user_id) or {}
                if pending.get("action") != "add_confirm":
                    raise ValueError("添加内容已过期，请重新操作")
                added, duplicates = self.url_config.add(pending["items"])
                self.pending.pop(user_id, None)
                text = f"✅ 已添加 {added} 条"
                if duplicates:
                    text += f"，跳过 {duplicates} 条重复链接"
                self._send(chat_id, text, [[("📋 查看列表", "list:0")]])
            elif action == "add_no":
                self.pending.pop(user_id, None)
                self._menu(chat_id, message_id)
            elif action == "show":
                self._detail(chat_id, message_id, parts[1], int(parts[2]), int(parts[3]))
            elif action in ("toggle", "delete"):
                revision, line_index, page = parts[1], int(parts[2]), int(parts[3])
                entry = self._find_entry(revision, line_index)
                verb = "恢复" if entry.paused else "暂停"
                if action == "delete":
                    verb = "删除"
                warning = "\n若该主播正在录制，此操作会让当前录制正常收尾后停止。" if not entry.paused else ""
                rows = [[(f"确认{verb}", f"{action}_yes:{revision}:{line_index}:{page}"), ("取消", f"show:{revision}:{line_index}:{page}")]]
                self._edit(chat_id, message_id, f"确认{verb}「{entry.display_name}」？{warning}", rows)
            elif action == "toggle_yes":
                revision, line_index, page = parts[1], int(parts[2]), int(parts[3])
                paused = self.url_config.toggle(revision, line_index)
                state = "已暂停" if paused else "已恢复"
                self._edit(chat_id, message_id, f"✅ {state}，主程序会自动重新读取", [[("📋 返回列表", f"list:{page}")]])
            elif action == "delete_yes":
                revision, line_index, page = parts[1], int(parts[2]), int(parts[3])
                self.url_config.delete(revision, line_index)
                self._edit(chat_id, message_id, "✅ 已删除，修改前配置已经备份", [[("📋 返回列表", f"list:{page}")]])
            elif action == "edit":
                revision, line_index, page = parts[1], int(parts[2]), int(parts[3])
                self._find_entry(revision, line_index)
                self._set_pending(
                    user_id,
                    action="edit_link",
                    revision=revision,
                    line_index=line_index,
                    page=page,
                )
                self._edit(chat_id, message_id, "请发送新的完整直播链接，发送 /cancel 取消")
            elif action == "qmenu":
                revision, line_index, page = parts[1], int(parts[2]), int(parts[3])
                self._find_entry(revision, line_index)
                rows = [
                    [(quality, f"quality:{revision}:{line_index}:{page}:{quality}") for quality in QUALITIES[:3]],
                    [(quality, f"quality:{revision}:{line_index}:{page}:{quality}") for quality in QUALITIES[3:]],
                    [("使用全局默认", f"quality:{revision}:{line_index}:{page}:默认"), ("取消", f"show:{revision}:{line_index}:{page}")],
                ]
                self._edit(chat_id, message_id, "请选择画质。已在监测的主播需暂停后再恢复，才会使用新画质。", rows)
            elif action == "quality":
                revision, line_index, page, quality = parts[1], int(parts[2]), int(parts[3]), parts[4]
                self.url_config.update_quality(revision, line_index, quality)
                self._edit(chat_id, message_id, f"✅ 画质已改为：{quality}", [[("📋 返回列表", f"list:{page}")]])
        except (ValueError, IndexError) as error:
            self._edit(chat_id, message_id, f"❌ {error}", [[("📋 刷新列表", "list:0")]])

    def _discard_backlog(self):
        while True:
            updates = self._api("getUpdates", {"timeout": 0, "offset": self.offset or 0}, timeout=10)
            if not updates:
                return
            self.offset = max(update["update_id"] for update in updates) + 1
            if len(updates) < 100:
                return

    def run(self):
        try:
            self._configure_commands()
        except Exception as error:
            print(f"Telegram 命令菜单注册失败: {error}")
        try:
            self._discard_backlog()
        except Exception as error:
            print(f"Telegram 管理器初始化失败: {error}")
        while True:
            try:
                updates = self._api(
                    "getUpdates",
                    {
                        "timeout": 25,
                        "offset": self.offset or 0,
                        "allowed_updates": ["message", "callback_query"],
                    },
                    timeout=35,
                )
                for update in updates:
                    self.offset = update["update_id"] + 1
                    if "callback_query" in update:
                        self._handle_callback(update["callback_query"])
                    elif "message" in update:
                        self._handle_message(update["message"])
            except Exception as error:
                print(f"Telegram 管理器错误: {error}")
                time.sleep(5)
