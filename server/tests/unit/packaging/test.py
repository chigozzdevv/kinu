import zipfile
from pathlib import Path

from infra.package import assemble


def test_rebuild_removes_deleted_source_and_dependency_files(tmp_path: Path) -> None:
    root, dependencies = tmp_path / "project", tmp_path / "dependencies"
    dependencies.mkdir()
    (dependencies / "old-library.py").write_text("old = True")
    for folder in (
        "server/worker",
        "packages/contracts/src/contracts",
        "packages/policy/src/policy",
        "packages/telemetry/src/telemetry",
    ):
        package = root / folder
        package.mkdir(parents=True)
        (package / "__init__.py").write_text("")
    source = root / "server/worker/deleted.py"
    source.write_text("old = True")
    target, archive = tmp_path / "lambda", tmp_path / "server.zip"
    assemble(root, dependencies, target, archive, True)
    source.unlink()
    (dependencies / "old-library.py").unlink()
    (root / "server/worker/current.py").write_text("current = True")
    assemble(root, dependencies, target, archive, True)
    with zipfile.ZipFile(archive) as built:
        assert "worker/current.py" in built.namelist()
        assert "worker/deleted.py" not in built.namelist()
        assert "old-library.py" not in built.namelist()
    assert not (target / "worker/deleted.py").exists()
