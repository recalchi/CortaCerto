import ast
import unittest
from pathlib import Path


class ArchitectureBoundaryTests(unittest.TestCase):
    def test_core_modules_do_not_import_ui(self) -> None:
        offenders: list[str] = []
        for path in sorted((Path("src") / "core").glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name == "src.ui" or alias.name.startswith("src.ui."):
                            offenders.append(f"{path}:{node.lineno}")
                elif isinstance(node, ast.ImportFrom):
                    module = node.module or ""
                    if module == "src.ui" or module.startswith("src.ui.") or node.level >= 2 and module == "ui":
                        offenders.append(f"{path}:{node.lineno}")

        self.assertEqual(offenders, [])

    def test_ui_is_allowed_to_import_core_services(self) -> None:
        app_text = (Path("src") / "ui" / "app.py").read_text(encoding="utf-8")

        self.assertIn("from ..core.timeline_model import", app_text)
        self.assertIn("from ..pipeline import", app_text)


if __name__ == "__main__":
    unittest.main()
