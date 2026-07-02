# Hermes Platform Overlay

This package is the AI Platform overlay for the upstream Hermes Agent desktop app.

Hermes itself lives in `vendor/hermes-agent` as a Git submodule. Treat that folder as upstream-owned code:

- do not edit files under `vendor/hermes-agent` for AI Platform behavior;
- update it with `npm run hermes:update`;
- keep AI Platform behavior in this overlay package, backend adapters, connector brokers, and configuration files outside the submodule.

## Local Commands

Initialize the upstream checkout and install Hermes desktop dependencies:

```bash
npm run hermes:init
```

Run the upstream Hermes desktop app:

```bash
npm run hermes:desktop
```

Check the pinned upstream commit:

```bash
npm run hermes:status
```

Move the submodule to the latest upstream commit on its tracked branch:

```bash
npm run hermes:update
```

## Integration Shape

The intended architecture is:

- `vendor/hermes-agent`: untouched upstream Hermes Agent.
- `apps/hermes-platform`: AI Platform overlay, route/adaptation docs, and future extension points.
- `apps/ai-core-api`: hosted auth, connector credentials, chat/session persistence, and workspace execution broker.
- `apps/web-portal`: current production web UI until the Hermes shell replaces or embeds it.

The near-term goal is to prove the Hermes desktop shell can talk to AI Platform services through a small adapter rather than continuing to port Hermes UI components one by one.
