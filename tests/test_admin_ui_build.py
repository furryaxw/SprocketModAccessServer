from __future__ import annotations

import os
import re
import subprocess
import unittest
from pathlib import Path


class AdminUiBuildTests(unittest.TestCase):
    def test_built_assets_are_relative_to_admin_mount(self) -> None:
        project = Path(__file__).parents[1] / "admin-ui"
        npm = "npm.cmd" if os.name == "nt" else "npm"
        subprocess.run(
            [npm, "run", "build"],
            cwd=project,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        html = (project / "dist" / "index.html").read_text(encoding="utf-8")
        references = re.findall(r"(?:src|href)=\"([^\"]+)\"", html)
        asset_references = [reference for reference in references if "/assets/" in reference]
        self.assertTrue(asset_references)
        self.assertTrue(all(reference.startswith("/admin/assets/") for reference in asset_references))


if __name__ == "__main__":
    unittest.main()
