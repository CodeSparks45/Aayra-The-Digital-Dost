from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core.config import get_settings
from app.api import routes_chat
from app.utils.logger import configure_logging, get_logger
from app.services.memory_neo4j import initialize_schema, close_driver

settings = get_settings()

@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging(log_level="INFO", dev_mode=not settings.is_production)
    log = get_logger("system")
    log.info("aayra_api_starting", version=settings.APP_VERSION)
    
    try:
        # Avoid crashing when using the dummy localhost URI
        if settings.NEO4J_URI and "localhost" not in settings.NEO4J_URI:
            await initialize_schema()
    except Exception as e:
        log.warning("neo4j_schema_init_failed", error=str(e))

    yield 

    log.info("aayra_api_shutting_down")
    if settings.NEO4J_URI and "localhost" not in settings.NEO4J_URI:
        await close_driver()

app = FastAPI(title=settings.APP_NAME, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(routes_chat.router, prefix=settings.API_PREFIX)

@app.get("/health")
async def health_check():
    return JSONResponse(content={"status": "healthy", "version": settings.APP_VERSION})