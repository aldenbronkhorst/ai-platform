# AI Platform Web Portal

React/Vite frontend for the AI Platform chat and connector experience.

## Local Development

```bash
npm ci --workspaces=false
npm run dev --workspaces=false
```

## Checks

```bash
npm run lint --workspaces=false
npm run build --workspaces=false
npx --yes knip
```

The portal API base URL defaults to the configured production APIM URL in `src/hooks/useApi.ts`. For local API testing, set `VITE_APIM_BASE_URL`.
