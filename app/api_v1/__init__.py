"""Phase 0 — versioned JSON API for the native app. Additive only: every
route here is new; no existing HTML route/template/business logic is
touched. Mounted at /api/v1 in app/main.py."""
from fastapi import APIRouter

from . import auth, tickets, tasks, home, employees, attendance, fms, sales, inventory, knowledge, devices

router = APIRouter(prefix="/api/v1")
router.include_router(auth.router)
router.include_router(tickets.router)
router.include_router(tasks.router)
router.include_router(home.router)
router.include_router(employees.router)
router.include_router(attendance.router)
router.include_router(fms.router)
router.include_router(sales.router)
router.include_router(inventory.router)
router.include_router(knowledge.router)
router.include_router(devices.router)
