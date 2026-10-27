from html import escape
from typing import Any


def page(content: str) -> str:
    return (
        """<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width"><title>Kinu demo provider</title><style>
body { font:16px system-ui; background:#f6f5f0; color:#232622;
       margin:40px auto; max-width:760px; padding:24px }
h1 { font-size:32px }
label { display:block; margin:18px 0 }
input,button { font:inherit; padding:12px; border:1px solid #b7bdb3; border-radius:8px }
button { background:#243a2a; color:white; cursor:pointer }
article { padding:20px; background:white; border:1px solid #ddd; border-radius:12px; margin:20px 0 }
code { overflow-wrap:anywhere }
small { color:#5e665c }
</style>"""
        + content
        + "</html>"
    )


def login() -> str:
    return page(
        "<h1>Demo provider</h1><p>This test service issues working demo API keys.</p>"
        '<form method="post" action="/login">'
        '<label>Workspace <input name="organisation_id" required></label>'
        '<label>Administrator password <input name="password" type="password" '
        'autocomplete="off" required></label><button>Sign in</button></form>'
    )


def keys(
    org: str, values: tuple[dict[str, Any], ...], csrf: str, created: tuple[str, str] | None = None
) -> str:
    content = f"<h1>API keys</h1><small>Demo workspace: {escape(org)}</small>"
    if created:
        identity, value = created
        content += (
            "<article><h2>New key created</h2><p>Key ID: "
            f'<code data-testid="created-id">{escape(identity)}</code></p><p>API key: '
            f'<code data-testid="created-secret" data-secret>{escape(value)}</code></p>'
            "<p>Capture this key into your secret store.</p></article>"
        )
    content += (
        '<article><form method="post" action="/keys">'
        f'<input type="hidden" name="csrf" value="{escape(csrf)}">'
        '<label>Request ID <input name="request_id" required maxlength="128"></label>'
        "<button>Create key</button></form></article>"
    )
    for item in values:
        identity = escape(item["id"])
        content += (
            f"<article><h2><code>{identity}</code></h2>"
            f'<p data-testid="key-state">{escape(item["state"])}</p><p>Scope: demo:read</p>'
            f'<form method="post" action="/keys/{identity}/revoke">'
            f'<input type="hidden" name="csrf" value="{escape(csrf)}">'
            f"<button>Retire key {identity}</button></form></article>"
        )
    return page(content)
