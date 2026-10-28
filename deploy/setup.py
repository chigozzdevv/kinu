import os
import secrets
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def setup() -> None:
    directory = ROOT / ".secrets"
    directory.mkdir(mode=0o700, exist_ok=True)
    directory.chmod(0o700)
    for name in ("auth", "vault", "native", "executor-token", "native-token", "mongo", "setup"):
        path = directory / name
        if not path.exists():
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w") as stream:
                stream.write(secrets.token_hex(48) + "\n")
        path.chmod(0o444)
    path = ROOT / ".env"
    existing = path.read_text() if path.exists() else ""
    keys = {line.partition("=")[0].strip() for line in existing.splitlines() if "=" in line}
    values = {
        "KINU_PUBLIC_URL": "http://localhost:8080",
        "KINU_SITE": ":80",
        "KINU_HTTP_PORT": "8080",
        "KINU_HTTPS_PORT": "8443",
        "KINU_ENVIRONMENT": "development",
        "KINU_RESEND_KEY": "",
        "KINU_EMAIL_SENDER": "Kinu <noreply@example.com>",
        **{
            f"KINU_MONGO_{role}_PASSWORD": secrets.token_hex(24)
            for role in ("ROOT", "API", "EXECUTOR", "NATIVE")
        },
    }
    missing = "".join(f"{key}={value}\n" for key, value in values.items() if key not in keys)
    if missing:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(descriptor, "w") as stream:
            stream.write(("\n" if existing and not existing.endswith("\n") else "") + missing)
    path.chmod(0o600)
    print("Configuration ready. Set your public URL and Resend settings in .env.")
    print("After starting Kinu, run python3 -m deploy.setup --link on this host.")


def link() -> None:
    import argparse
    import webbrowser
    from urllib.parse import quote

    parser = argparse.ArgumentParser()
    parser.add_argument("--link", action="store_true")
    options = parser.parse_args()
    if not options.link:
        setup()
        return
    values = dict(
        line.split("=", 1)
        for line in (ROOT / ".env").read_text().splitlines()
        if "=" in line and not line.lstrip().startswith("#")
    )
    origin = values.get("KINU_PUBLIC_URL", "http://localhost:8080").strip().rstrip("/")
    token = (ROOT / ".secrets/setup").read_text().strip()
    destination = origin + "/auth#setup=" + quote(token, safe="")
    print(destination)
    webbrowser.open(destination)


if __name__ == "__main__":
    link()
