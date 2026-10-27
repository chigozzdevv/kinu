import argparse
import shutil
import zipfile
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", action="store_true", help="Omit the remote browser driver")
    options = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    target = root / ".build/lambda"
    for directory in (root / "server").iterdir():
        if (
            directory.is_dir()
            and (directory / "__init__.py").exists()
            and directory.name != "tests"
        ):
            if (target / directory.name).exists():
                shutil.rmtree(target / directory.name)
            shutil.copytree(
                directory,
                target / directory.name,
                dirs_exist_ok=True,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
            )
    for package in ("contracts", "policy", "telemetry"):
        shutil.copytree(
            root / f"packages/{package}/src/{package}",
            target / package,
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
    archive = root / ".build/server.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as output:
        for path in sorted(target.rglob("*")):
            if options.api and path.is_relative_to(target / "playwright/driver"):
                continue
            if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc":
                output.write(path, path.relative_to(target))
    print(f"Lambda package: {archive.stat().st_size // 1024 // 1024} MiB")


if __name__ == "__main__":
    main()
