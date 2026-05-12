from __future__ import annotations

import argparse
from pathlib import Path
import re
import subprocess
import sys


LEGACY_ROOT_FILES = [
    "test.py",
    "test_tkinter.py",
    "test_video_editor.py",
    "testlib.py",
    "video_editor.py",
    "video_editor_final.py",
    "video_editor_gui.py",
]

TEXT_ENCODING_CHECK_GLOBS = [
    "README.md",
    "main.py",
    "install.bat",
    "run.bat",
    "installer/*.bat",
    "installer/*.iss",
    "scripts/*.py",
    "src/**/*.py",
    "tests/*.py",
]

MOJIBAKE_MARKERS = ["\u00c3", "\u00c2", "\ufffd"]

SECRET_PATTERNS = [
    re.compile(r"sk-proj-[A-Za-z0-9_-]{20,}"),
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"OPENAI_API_KEY\s*=\s*[\"']?sk-", re.IGNORECASE),
]


def safe_console(text: str) -> str:
    return text.encode("ascii", errors="backslashreplace").decode("ascii")

CHECKS: list[tuple[str, list[str]]] = [
    (
        "Compilacao dos modulos principais",
        [
            sys.executable,
            "-m",
            "py_compile",
            "main.py",
            "src/api_settings.py",
            "src/bootstrap.py",
            "src/core/ai_assistant.py",
            "src/pipeline.py",
            "src/ui/app.py",
            "tests/test_bootstrap.py",
            "tests/test_api_settings.py",
            "tests/test_ai_assistant.py",
            "tests/test_effect_renderer.py",
            "tests/test_export_smoke.py",
            "tests/test_editor_consistency.py",
            "tests/test_ffmpeg_env.py",
            "tests/test_pipeline_cleanup.py",
            "tests/test_preview_ui.py",
            "tests/test_sprint_checks.py",
        ],
    ),
    (
        "Testes unitarios e invariantes do editor",
        [sys.executable, "-m", "unittest", "discover", "-s", "tests"],
    ),
]


def run_check(title: str, cmd: list[str]) -> int:
    print(f"\n[CHECK] {title}")
    print(" ".join(cmd))
    result = subprocess.run(cmd)
    if result.returncode == 0:
        print(f"[OK] {title}")
    else:
        print(f"[ERRO] {title} falhou com codigo {result.returncode}")
    return result.returncode


def check_legacy_root_files(strict: bool, print_fn=print) -> int:
    found = [path for path in LEGACY_ROOT_FILES if Path(path).exists()]
    if not found:
        print_fn("\n[OK] Nenhum arquivo legado conhecido na raiz.")
        return 0

    print_fn("\n[AVISO] Arquivos legados conhecidos ainda existem na raiz:")
    for path in found:
        print_fn(f"  - {path}")
    print_fn("Use sempre: python scripts\\run_sprint_checks.py")
    print_fn("Evite rodar unittest discover sem '-s tests', pois esses arquivos nao pertencem a suite atual.")
    if strict:
        return 1
    return 0


def iter_text_files() -> list[Path]:
    files: list[Path] = []
    for pattern in TEXT_ENCODING_CHECK_GLOBS:
        files.extend(Path(".").glob(pattern))
    return sorted({path for path in files if path.is_file()})


def check_text_encoding(print_fn=print) -> int:
    failures: list[str] = []
    for path in iter_text_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        markers = [marker for marker in MOJIBAKE_MARKERS if marker in text]
        if markers:
            escaped_markers = " ".join(safe_console(marker) for marker in markers)
            failures.append(f"{path}: {escaped_markers}")

    if not failures:
        print_fn("\n[OK] Textos ativos sem mojibake comum.")
        return 0

    print_fn("\n[ERRO] Possivel texto com encoding quebrado:")
    for failure in failures:
        print_fn(f"  - {safe_console(failure)}")
    return 1


def check_test_inventory(print_fn=print) -> int:
    test_files = sorted(Path("tests").glob("test_*.py"))
    test_count = 0
    for path in test_files:
        text = path.read_text(encoding="utf-8", errors="replace")
        test_count += text.count("def test_")
    print_fn(f"\n[OK] Inventário de testes: {len(test_files)} arquivos, {test_count} casos declarados.")
    return 0


def check_secret_leaks(print_fn=print) -> int:
    failures: list[str] = []
    for path in iter_text_files():
        if path.name == ".env" or path.suffix == ".env":
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if any(pattern.search(text) for pattern in SECRET_PATTERNS):
            failures.append(str(path))
    if not failures:
        print_fn("\n[OK] Nenhum segredo conhecido em arquivos rastreáveis.")
        return 0
    print_fn("\n[ERRO] Possível segredo encontrado fora de .env:")
    for failure in failures:
        print_fn(f"  - {failure}")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Executa a bateria curta da sprint CortaCerto.")
    parser.add_argument(
        "--include-startup",
        action="store_true",
        help="Também valida FFmpeg real com main.py --check-startup.",
    )
    parser.add_argument(
        "--include-export-smoke",
        action="store_true",
        help="Tambem gera videos sinteticos e valida um export real curto.",
    )
    parser.add_argument(
        "--strict-legacy",
        action="store_true",
        help="Falha se arquivos legados conhecidos ainda existirem na raiz.",
    )
    args = parser.parse_args()

    checks = list(CHECKS)
    if args.include_startup:
        checks.append(
            (
                "Startup real com FFmpeg",
                [sys.executable, "main.py", "--check-startup"],
            )
        )
    if args.include_export_smoke:
        checks.append(
            (
                "Export real sintetico",
                [
                    sys.executable,
                    "-c",
                    "import os, unittest; os.environ['CORTACERTO_EXPORT_SMOKE']='1'; unittest.main(module='tests.test_export_smoke')",
                ],
            )
        )

    for title, cmd in checks:
        code = run_check(title, cmd)
        if code != 0:
            return code

    code = check_legacy_root_files(args.strict_legacy)
    if code != 0:
        return code

    code = check_text_encoding()
    if code != 0:
        return code

    code = check_secret_leaks()
    if code != 0:
        return code

    code = check_test_inventory()
    if code != 0:
        return code

    print("\n[OK] Sprint checks concluidos.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
