import tempfile
import unittest
from pathlib import Path

from telegram_manager import TelegramManager, URLConfig


class URLConfigTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.path = root / "config" / "URL_config.ini"
        self.path.parent.mkdir()
        self.path.write_text(
            "https://live.douyin.com/100,主播: 甲\n"
            "#超清,https://live.douyin.com/200,主播: 乙\n",
            encoding="utf-8-sig",
        )
        self.config = URLConfig(str(self.path), str(root / "backup"))

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_crud_preserves_supported_format_and_rejects_stale_buttons(self):
        revision, entries = self.config.snapshot()
        self.assertEqual([item.display_name for item in entries], ["甲", "乙"])
        self.assertFalse(entries[0].paused)
        self.assertTrue(entries[1].paused)
        self.assertEqual(entries[1].quality, "超清")

        self.assertTrue(self.config.toggle(revision, entries[0].line_index))
        with self.assertRaisesRegex(ValueError, "列表已经变化"):
            self.config.delete(revision, entries[1].line_index)

        revision, entries = self.config.snapshot()
        self.config.update_quality(revision, entries[0].line_index, "高清")
        revision, entries = self.config.snapshot()
        self.config.update_url(revision, entries[0].line_index, "https://live.douyin.com/101")

        valid, invalid = self.config.parse_additions(
            "https://live.douyin.com/101\n原画,https://live.douyin.com/300\n不是链接"
        )
        self.assertEqual(len(valid), 2)
        self.assertEqual(invalid, ["不是链接"])
        added, duplicates = self.config.add(valid)
        self.assertEqual((added, duplicates), (1, 1))

        revision, entries = self.config.snapshot()
        third = next(item for item in entries if item.url.endswith("/300"))
        self.config.delete(revision, third.line_index)
        final_text = self.path.read_text(encoding="utf-8-sig")
        self.assertIn("#高清,https://live.douyin.com/101", final_text)
        self.assertNotIn("/300", final_text)
        self.assertTrue(any((Path(self.temp_dir.name) / "backup").iterdir()))

    def test_list_uses_eight_items_per_page(self):
        self.path.write_text(
            "".join(f"https://live.douyin.com/{index}\n" for index in range(10)),
            encoding="utf-8-sig",
        )
        manager = TelegramManager("test", {1}, {1}, self.config, lambda: [])
        calls = []
        manager._api = lambda method, payload, timeout=20: calls.append((method, payload))
        manager._list(1, 0)
        method, payload = calls[-1]
        self.assertEqual(method, "sendMessage")
        rows = payload["reply_markup"]["inline_keyboard"]
        streamer_buttons = [row for row in rows if row[0]["callback_data"].startswith("show:")]
        self.assertEqual(len(streamer_buttons), 8)
        self.assertTrue(any(button["callback_data"] == "list:1" for row in rows for button in row))


if __name__ == "__main__":
    unittest.main()
