from fastapi import FastAPI
from app.routers import health, audit, artifact, context, job, task, tool, odoo, connected_accounts

app = FastAPI(
    title="AI Platform Core API",
    version="0.1.0",
    description="Central operating layer for AI interfaces and tools",
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


@app.get("/")
async def root():
    return {
        "name": "AI Platform Core API",
        "version": "0.1.0",
        "docs": "/docs",
    }
