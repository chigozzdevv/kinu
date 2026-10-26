# Kinu client

Existing React, TypeScript, Tailwind CSS, and Vite UI. The layout is retained for a later
UI pass. Branding and browser storage keys use Kinu. Authentication is an injectable
interface in `src/lib/auth.ts`; no provider or local sign-in bypass is configured.

```sh
npm ci
cp .env.example .env.local
npm run dev
npm run check
```

Vite proxies `/v1` and `/health` to `KINU_DEV_API_URL` (default: http://127.0.0.1:8000).
Keep `VITE_API_URL` empty for that proxy. Use `configureAuthentication` before rendering
when a real identity adapter is implemented. The AWS connection card remains disabled
until its onboarding implementation exists.
