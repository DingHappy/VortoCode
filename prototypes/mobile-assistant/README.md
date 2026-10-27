# VortoCode mobile interaction prototype

Independent Product Design mobile-app prototype for the approved Muse-informed structure in [MOBILE_APP_DESIGN.md](../../docs/MOBILE_APP_DESIGN.md). It does not modify or replace the installed VortoCode Desktop application.

Main screens: chat, work activity, and assistant details. Goals and artifacts provide read-only design examples. The prototype also demonstrates session switching, message drafts, a two-step decision and receipt, connection loss, settings, and the template's iPhone/Pixel keyboard runtime.

All messages, connections, approvals, notification preferences, schedules and names are local React state. There are no backend requests, model calls, credentials, real approvals, OS notification permission changes, or server deployments. Reloading resets demonstration state.

```sh
npm ci --prefer-offline --no-audit --no-fund
npm run dev -- --host 127.0.0.1 --port 5215 --strictPort
npm run build
```

Preview routes for the same prototype: `/` (chat), `/?screen=feed` (work activity), `/?screen=assistant` (assistant records). Device chrome, safe areas, keyboard and device picker remain the template-owned runtime; `npm run check:runtime` verifies those files.

The supplied Muse screenshots are kept outside this project in an ignored local review folder. They are reference material, not distributable app assets. The app uses the existing VortoCode logo and the template's Radix icon library. Do not publish personal reference images with this prototype.

See `design-qa.md` for browser checks, visual comparisons, intentional adaptations and outstanding boundaries.
