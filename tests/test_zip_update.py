"""发布包更新回归测试：只从 ZIP 读取目标 EXE。"""

import hashlib
import io
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from subtitle_translator import updater


def package_bytes(executable_name):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(executable_name, b"MZ" + b"\0" * (1024 * 1024))
    return output.getvalue()


class ZipUpdateTests(unittest.TestCase):
    def test_release_selects_zip_asset(self):
        package = {"name": "srt-Zh2En-v1.0.2-package.zip", "size": 100}
        release = {"tag_name": "v1.0.2", "assets": [package], "body": "", "html_url": ""}
        with patch.object(updater, "_request_json", return_value=release):
            found = updater.check_latest_release(current_version="1.0.1")
        self.assertTrue(found["available"])
        self.assertEqual(found["asset"], package)

    def test_download_extracts_only_program_from_zip(self):
        payload = package_bytes(updater.EXE_NAME)
        asset = {
            "name": "srt-Zh2En-v1.0.2-package.zip",
            "browser_download_url": "https://github.com/yujianvip/srt-Zh2En/releases/download/v1.0.2/package.zip",
            "size": len(payload),
            "digest": "sha256:" + hashlib.sha256(payload).hexdigest(),
        }
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(updater.tempfile, "gettempdir", return_value=folder), \
                    patch.object(updater.urllib.request, "urlopen", return_value=io.BytesIO(payload)):
                result = updater.download_release({"asset": asset})
            try:
                self.assertEqual(result.read_bytes()[:2], b"MZ")
                self.assertEqual([path.suffix for path in Path(folder).iterdir()], [".exe"])
            finally:
                result.unlink(missing_ok=True)

    def test_missing_program_is_rejected_and_cleans_temp_files(self):
        payload = package_bytes("unrelated.exe")
        asset = {
            "browser_download_url": "https://github.com/yujianvip/srt-Zh2En/releases/download/v1.0.2/package.zip",
            "size": len(payload),
        }
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(updater.tempfile, "gettempdir", return_value=folder), \
                    patch.object(updater.urllib.request, "urlopen", return_value=io.BytesIO(payload)):
                with self.assertRaisesRegex(RuntimeError, "缺少"):
                    updater.download_release({"asset": asset})
            self.assertEqual(list(Path(folder).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
