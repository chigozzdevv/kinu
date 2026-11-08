# Kinu Vercel demo

A small live view for the Resend key loaded by this Vercel deployment. The page shows the deployment ID and a short SHA-256 fingerprint; the raw key stays in the server function.

## Vercel project

Set the project root directory to `demo/vercel`. Add `RESEND_API_KEY` as a Production environment variable. Enable Vercel's automatically exposed system environment variables so the status page can show `VERCEL_DEPLOYMENT_ID`.

The app has no build step or third-party dependencies. `/api/status` returns the live deployment ID and short fingerprint. Kinu can call that same HTTPS endpoint with `X-Kinu-Probe`; the function checks that the key is a Resend sending key and returns the full fingerprint for Kinu's nonce-bound runtime proof. The raw key is never returned.

For local Vercel development, run `vercel dev` from this directory with `RESEND_API_KEY` set in the environment.
