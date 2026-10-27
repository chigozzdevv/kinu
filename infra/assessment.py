import json
import subprocess
from pathlib import Path


def bundle(target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    package = json.loads(Path("agent/node_modules/@agent-native/core/package.json").read_text())
    optional = [
        f"--external:{name}"
        for name, meta in package["peerDependenciesMeta"].items()
        if meta.get("optional") and not (Path("agent/node_modules") / name).exists()
    ]
    subprocess.run(
        [
            "agent/node_modules/.bin/esbuild",
            "agent/cloud/assessment.ts",
            "--bundle",
            "--platform=node",
            "--target=node22",
            "--format=esm",
            f"--outfile={target}",
            *optional,
            '--banner:js=import { createRequire as __kinuCreateRequire } from "node:module"; '
            "const require = __kinuCreateRequire(import.meta.url);",
        ],
        check=True,
    )
    subprocess.run(["node", "--check", str(target)], check=True)
    subprocess.run(
        [
            "node",
            "--input-type=module",
            "--eval",
            "const module = await import(process.argv[1]); "
            "if (typeof module.handler !== 'function') "
            "throw new Error('Missing assessment handler');",
            target.resolve().as_uri(),
        ],
        check=True,
    )


if __name__ == "__main__":
    bundle(Path(".build/assessment.mjs"))
