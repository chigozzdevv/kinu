# Kinu demo on Railway

The Vercel demo (`demo/vercel`) served as a normal container.

1. Railway → New Project → Deploy from GitHub → this repo.
2. Service → Settings → **Root Directory** = `demo/railway` (Dockerfile build).
3. Variables: `RESEND_API_KEY` (any placeholder until Kinu writes it).
4. Generate a public domain; health URL is `https://<domain>/api/status`.

Then in Kinu: Hosting connection → Railway → approve the project → Add credential →
select the service variable `RESEND_API_KEY` → bind the Resend key → health URL above.
