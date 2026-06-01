import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.routers import health, audit, artifact, context, job, task, tool, odoo, connected_accounts, chat, ai_config, memory, rules, admin_traces

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: seed tools
    try:
        from scripts.seed_tools import seed_tools
        await seed_tools()
        logger.info("Tools seeded successfully on startup")
    except Exception as exc:
        logger.warning("Tool seeding on startup failed (non-fatal): %s", exc)
    yield


app = FastAPI(
    title="AI Platform Core API",
    version="0.1.0",
    description="Central operating layer for AI interfaces and tools",
    lifespan=lifespan,
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
app.include_router(audit.router)
app.include_router(artifact.router)
app.include_router(context.router)
app.include_router(job.router)
app.include_router(task.router)
app.include_router(tool.router)
app.include_router(odoo.router, prefix="/tools/odoo", tags=["Odoo Tools"])
app.include_router(connected_accounts.router)
app.include_router(chat.router)
app.include_router(ai_config.router)
app.include_router(memory.router)
app.include_router(rules.router)
app.include_router(admin_traces.router)


@app.get("/")
async def root():
    return {
        "name": "AI Platform Core API",
        "version": "0.1.0",
        "docs": "/docs",
    }
