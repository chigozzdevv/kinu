from html import escape


def verification(code: str, purpose: str) -> tuple[str, str, str]:
    title = "Reset your password" if purpose == "reset" else "Verify your email"
    footer = "If you didn't request this, ignore this email."
    text = f"{title}\n\nYour Kinu code is {code}. It expires in 10 minutes.\n\n{footer}"
    html = f"""<!doctype html><html><body style="margin:0;background:#f7f7f5;
font-family:Arial,sans-serif;color:#171717">
<div style="max-width:440px;margin:40px auto;padding:32px;background:white;border-radius:12px">
<strong style="font-size:28px">kinu</strong>
<h1 style="font-size:22px;margin-top:32px">{escape(title)}</h1>
<p>Your code:</p>
<p style="font-size:32px;letter-spacing:8px;font-weight:600">{escape(code)}</p>
<p>Expires in 10 minutes.</p>
<p style="font-size:13px;color:#666;margin-top:32px">{escape(footer)}</p>
</div></body></html>"""
    return title, text, html
