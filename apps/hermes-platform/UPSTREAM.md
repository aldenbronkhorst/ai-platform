# Upstream Policy

Source: `https://github.com/nousresearch/hermes-agent.git`

License: MIT. The upstream license remains in `vendor/hermes-agent/LICENSE`.

## Rules

1. `vendor/hermes-agent` is upstream-owned. Do not make product changes there.
2. AI Platform-specific work belongs outside the submodule.
3. If a change is useful to Hermes itself, make it as a separate upstream contribution, then pull it back through the submodule.
4. Keep local integration thin: configuration, adapters, auth bridge, connector bridge, and branding.
5. Before updating upstream, run the current local app tests and note any known breakages. After updating, run them again.

## Update Flow

```bash
npm run hermes:status
npm run hermes:update
npm run hermes:status
```

Then test the overlay and commit the changed submodule pointer.
