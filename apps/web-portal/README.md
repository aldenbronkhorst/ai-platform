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

The portal API base URL is read from `VITE_API_BASE_URL`. For local API testing, set it to the local FastAPI URL, for example `http://localhost:8000`.
