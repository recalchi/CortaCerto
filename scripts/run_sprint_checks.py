from __future__ import annotations

import argparse
import subprocess
import sys


CHECKS: list[tuple[str, list[str]]] = [
    (
        "Compilação dos módulos principais",
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
        ],
    ),
    (
        "Testes unitários e invariantes do editor",
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
        print(f"[ERRO] {title} falhou com código {result.returncode}")
    return result.returncode


def main() -> int:
    parser = argparse.ArgumentParser(description="Executa a bateria curta da sprint CortaCerto.")
    parser.add_argument(
        "--include-startup",
        action="store_true",
        help="Também valida FFmpeg real com main.py --check-startup.",
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

    print("\n[OK] Sprint checks concluídos.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
