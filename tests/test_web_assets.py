from __future__ import annotations

import re
import unittest
from pathlib import Path


WEB = Path(__file__).parents[1] / "src" / "trajectory_workbench" / "web"


class WebAssetsTest(unittest.TestCase):
    def test_shell_references_assets_and_labels_controls(self) -> None:
        index = (WEB / "index.html").read_text(encoding="utf-8")
        self.assertIn('href="/styles.css"', index)
        self.assertIn('src="/app.js"', index)
        self.assertIn('aria-label="绝对路径"', index)
        self.assertIn('aria-label="主导航"', index)
        for view in ("library", "compare", "stats", "queue"):
            self.assertIn(f'data-nav="{view}"', index)

    def test_no_module_parses_transcript_text_as_html(self) -> None:
        scripts = [WEB / "app.js", WEB / "api.js", WEB / "dom.js", WEB / "presentation.mjs", *sorted((WEB / "views").glob("*.js"))]
        self.assertGreater(len(scripts), 8)
        for script in scripts:
            source = script.read_text(encoding="utf-8")
            with self.subTest(script=script.name):
                self.assertNotRegex(source, r"\.(innerHTML|outerHTML)\s*=|insertAdjacentHTML|document\.write")
        dom = (WEB / "dom.js").read_text(encoding="utf-8")
        self.assertIn("element.textContent = value", dom)
        self.assertIn("document.createTextNode", dom)

    def test_api_is_same_origin(self) -> None:
        api = (WEB / "api.js").read_text(encoding="utf-8")
        self.assertIn('const API_ROOT = "/api"', api)
        self.assertIsNone(re.search(r"https?://", api))

    def test_every_imported_module_exists(self) -> None:
        for script in [WEB / "app.js", *sorted((WEB / "views").glob("*.js"))]:
            for target in re.findall(r'from "(\.[^"]+)"', script.read_text(encoding="utf-8")):
                with self.subTest(script=script.name, target=target):
                    self.assertTrue((script.parent / target).resolve().is_file())


if __name__ == "__main__":
    unittest.main()
