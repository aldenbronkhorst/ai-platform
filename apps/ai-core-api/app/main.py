from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.core.config import get_settings
from app.routers import health, artifact, tool, connected_accounts, chat, memory, connector_microsoft_native, connector_github, voice

settings = get_settings()
docs_enabled = settings.app_env != "production"

app = FastAPI(
    title="AI Platform Core API",
    version="0.1.0",
    description="Central operating layer for AI interfaces and tools",
    docs_url="/docs" if docs_enabled else None,
    redoc_url="/redoc" if docs_enabled else None,
    openapi_url="/openapi.json" if docs_enabled else None,
)

# Enforce secure CORS rules
origins = [
    "http://localhost:5173",
    "https://ai.lotslotsmore.com",
    "https://witty-forest-06e404603.7.azurestaticapps.net"
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-API-Key", "X-User-Id", "X-Request-ID"],
)

app.include_router(health.router)
app.include_router(artifact.router)
app.include_router(tool.router)
app.include_router(connected_accounts.router)
app.include_router(chat.router)
app.include_router(memory.router)
app.include_router(connector_microsoft_native.router)
app.include_router(connector_github.router)
app.include_router(voice.router)


@app.get("/")
async def root():
    return {
        "name": "AI Platform Core API",
        "version": "0.1.0",
        "docs": "/docs",
    }
