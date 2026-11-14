# Prototype Instructions

## Owner decisions, 2026-11-14

- Use the supplied VortoOne family: coral general assistant, purple research assistant with glasses, green developer with headset, yellow calendar assistant with scarf.
- Learn agentes.chat installation, onboarding, navigation and settings as one coherent personal experience. Independently implement; retain VortoOne branding and the current license.
- One personal space per runtime; multiple explicitly registered projects. No company invitations, new public CLI, automatic staff creation or permission expansion.
- This is a synthetic, in-memory design artifact. Do not connect to production, install services, call models, publish, or deploy.
- Owner clarification: closely recreate agentes.chat's visual structure and interaction patterns. Old layouts, duplicate entry points and verbose explanations may be discarded; do not keep them merely for continuity. Preserve VortoOne branding, the selected role family, existing data and all permission contracts. Implement independently, without copying upstream code or assets. The current revision remains a design prototype; production migration is separate.
- Owner feedback: add channel-style topic conversations. Keep personal mode and the main conversation, with a channel list, independent drafts/history, topic details, and replies attached to a message. Channels do not grant specialist permissions, share history automatically, or introduce company invitations. Use synthetic records only.

Run the local server yourself and open the preview in the browser available to this environment. Do not give the user server-start instructions when you can run it.

Before making substantial visual changes, use the Product Design plugin's `get-context` skill when the visual source is unclear or no longer matches the current goal. When the user gives durable prototype-specific design feedback, preferences, or decisions, record them in `AGENTS.md`.

When implementing from a selected generated mock, treat that image as the source of truth for layout, component anatomy, density, spacing, color, typography, visible content, and hierarchy.

Build app UI in `src/`. Keep `.openai/hosting.json`, `worker/index.js`, `scripts/prepare-sites-build.mjs`, and `tests/sites-worker.test.mjs` intact so the same local prototype can be handed to Sites. Before a Sites handoff, run `npm run build` and `npm run test:sites`; the build must leave `dist/client/index.html`, `dist/server/index.js`, and `dist/.openai/hosting.json`.
