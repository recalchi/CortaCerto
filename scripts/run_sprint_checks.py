from __future__ import annotations

import argparse
from pathlib import Path
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

CHECKS: list[tuple[str, list[str]]] = [
    (
        "Compilacao dos modulos principais",
        [
            sys.executable,
            "-m",
            "py_compile",
            "main.py",
            "src/bootstrap.py",
            "src/pipeline.py",
            "src/ui/app.py",
            "tests/test_bootstrap.py",
            "tests/test_editor_consistency.py",
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


def main() -> int:
    parser = argparse.ArgumentParser(description="Executa a bateria curta da sprint CortaCerto.")
    parser.add_argument(
        "--include-startup",
        action="store_true",
        help="Também valida FFmpeg real com main.py --check-startup.",
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

    for title, cmd in checks:
        code = run_check(title, cmd)
        if code != 0:
            return code

    code = check_legacy_root_files(args.strict_legacy)
    if code != 0:
        return code

    print("\n[OK] Sprint checks concluidos.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
