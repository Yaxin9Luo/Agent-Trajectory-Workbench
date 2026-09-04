from __future__ import annotations

import unittest
from pathlib import Path


WEB = (
    Path(__file__).parents[1]
    / "src"
    / "trajectory_workbench"
    / "web"
)


class WebAssetsTest(unittest.TestCase):
    def test_frontend_assets_and_accessible_controls_exist(self) -> None:
        index = (WEB / "index.html").read_text(encoding="utf-8")

        self.assertIn('href="/styles.css"', index)
        self.assertIn('src="/app.js"', index)
        self.assertIn('aria-label="MoH run 绝对路径"', index)
        self.assertIn('aria-label="工具筛选"', index)
        self.assertIn('aria-label="搜索轨迹"', index)

    def test_api_is_same_origin_and_model_content_uses_text_content(self) -> None:
        api = (WEB / "api.js").read_text(encoding="utf-8")
        app = (WEB / "app.js").read_text(encoding="utf-8")

        self.assertIn('const API_ROOT = "/api"', api)
        self.assertNotIn("innerHTML = message.text", app)
        self.assertIn(".textContent = message.text", app)


if __name__ == "__main__":
    unittest.main()

