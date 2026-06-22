
from fastapi import FastAPI
import asyncio

app = FastAPI()

@app.on_event('startup')
async def startup():
    print('STARTUP BEGIN', flush=True)
    from alembic.config import Config
    from alembic import command
    cfg = Config('alembic.ini')
    command.upgrade(cfg, 'head')
    print('ALEMBIC DONE', flush=True)
    from app.database import create_tables
    create_tables()
    print('CREATE_TABLES DONE', flush=True)
    from app.library_seeds import seed_library
    from app.database import SessionLocal
    db = SessionLocal()
    seed_library(db)
    db.close()
    print('SEED DONE', flush=True)
    from app.scheduler import start_scheduler
    start_scheduler()
    print('SCHEDULER DONE', flush=True)
    print('STARTUP COMPLETE', flush=True)
