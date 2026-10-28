import argparse
import shutil
import subprocess
import zipfile
from pathlib import Path


def assemble(root: Path, dependencies: Path, target: Path, archive: Path, api: bool) -> None:
    shutil.rmtree(target, ignore_errors=True)
    shutil.copytree(dependencies, target)
    for directory in (root / "server").iterdir():
        if (
            directory.is_dir()
            and (directory / "__init__.py").exists()
            and directory.name != "tests"
        ):
            shutil.copytree(
                directory,
                target / directory.name,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
            )
    for name in ("contracts", "policy", "telemetry"):
        shutil.copytree(
            root / f"packages/{name}/src/{name}",
            target / name,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as output:
        for path in sorted(target.rglob("*")):
            if api and path.is_relative_to(target / "playwright/driver"):
                continue
            if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc":
                output.write(path, path.relative_to(target))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", action="store_true", help="Omit the remote browser driver")
    options = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    build = root / ".build"
    build.mkdir(exist_ok=True)
    requirements = build / "requirements.txt"
    subprocess.run(
        [
            "uv",
            "export",
            "--locked",
            "--package",
            "kinu-server",
            "--no-dev",
            "--no-emit-workspace",
            "--extra",
            "aws",
            "--extra",
            "browser",
            "--output-file",
            str(requirements),
        ],
        cwd=root,
        check=True,
        stdout=subprocess.DEVNULL,
    )
    dependencies = build / "dependencies"
    shutil.rmtree(dependencies, ignore_errors=True)
    subprocess.run(
        [
            "uv",
            "pip",
            "install",
            "--target",
            str(dependencies),
            "--python-version",
            "3.12",
            "--python-platform",
            "x86_64-manylinux_2_34",
            "--only-binary",
            ":all:",
            "--no-deps",
            "--require-hashes",
            "--requirements",
            str(requirements),
        ],
        cwd=root,
        check=True,
    )
    archive = build / "server.zip"
    assemble(root, dependencies, build / "lambda", archive, options.api)
    print(f"Lambda package: {archive.stat().st_size // 1024 // 1024} MiB")


if __name__ == "__main__":
    main()
