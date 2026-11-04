from html import escape
from urllib.parse import urlsplit


def _layout(title: str, preview: str, content: str, origin: str, footer: str) -> str:
    logo = escape(origin.rstrip("/") + "/email-logo.png", quote=True)
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(title)}</title>
<style>
@media only screen and (max-width:600px) {{
  .email-outer {{ padding:24px 12px !important; }}
  .email-content {{ padding:0 24px 32px !important; }}
}}
</style>
</head>
<body style="margin:0;padding:0;background:#f6f6f6;color:#171717;
font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif">
<div style="display:none;max-height:0;overflow:hidden;mso-hide:all">{escape(preview)}</div>
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0"
style="background:#f6f6f6">
<tr><td class="email-outer" align="center" style="padding:48px 20px">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0"
style="max-width:560px;background:#ffffff;border:1px solid #e5e5e5;border-radius:12px">
<tr><td align="center" style="padding:36px 24px 32px">
<img src="{logo}" alt="Kinu" width="96" height="39"
style="display:block;width:96px;height:auto;border:0;color:#171717;font-size:28px;font-weight:700">
</td></tr>
<tr><td class="email-content" style="padding:0 40px 40px">
<h1 style="margin:0 0 12px;font-size:24px;line-height:32px;letter-spacing:-0.5px;
font-weight:600;color:#171717">{escape(title)}</h1>
{content}
</td></tr></table>
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0"
style="max-width:480px">
<tr><td align="center" style="padding:24px 16px 0;font-size:12px;line-height:20px;color:#737373">
{escape(footer)}
</td></tr></table>
</td></tr></table>
</body></html>"""


def message(title: str, body: str, url: str, invitation: bool = False) -> tuple[str, str, str]:
    action = "Join workspace" if invitation else "Open Kinu"
    text = f"{title}\n\n{body}\n\n{action}: {url}"
    paragraphs = "".join(
        '<p style="margin:0 0 16px;font-size:15px;line-height:25px;color:#525252">'
        + escape(part).replace("\n", "<br>")
        + "</p>"
        for part in body.split("\n\n")
        if part.strip()
    )
    content = f"""{paragraphs}
<table role="presentation" cellspacing="0" cellpadding="0" border="0" style="margin-top:28px">
<tr><td bgcolor="#171717" style="border-radius:6px">
<a href="{escape(url, quote=True)}" style="display:inline-block;padding:13px 22px;
border:1px solid #171717;border-radius:6px;color:#ffffff;font-size:14px;line-height:20px;
font-weight:600;text-decoration:none">{action}</a>
</td></tr></table>"""
    target = urlsplit(url)
    html = _layout(title, body, content, f"{target.scheme}://{target.netloc}", "Sent by Kinu.")
    return title, text, html


def verification(code: str, purpose: str, origin: str) -> tuple[str, str, str]:
    reset = purpose == "reset"
    title = "Reset your password" if reset else "Verify your email"
    description = (
        "Enter this code in Kinu to reset your password."
        if reset
        else "Enter this code in Kinu to verify your email."
    )
    footer = "If you didn't request this, you can ignore this email."
    text = f"{title}\n\nYour Kinu code is {code}. It expires in 10 minutes.\n\n{footer}"
    content = f"""<p style="margin:0 0 28px;font-size:15px;line-height:25px;color:#525252">
{description}</p>
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0">
<tr><td align="center" style="padding:20px 16px;background:#f5f5f5;border:1px solid #ebebeb;
border-radius:8px;font-family:'Courier New',monospace;font-size:34px;line-height:44px;
font-weight:600;letter-spacing:8px;color:#171717">{escape(code)}</td></tr>
</table>
<p style="margin:16px 0 0;text-align:center;font-size:13px;line-height:20px;color:#737373">
This code expires in 10 minutes.</p>"""
    html = _layout(title, description, content, origin, footer)
    return title, text, html
