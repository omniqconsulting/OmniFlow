"""Phase 0 — versioned JSON API for the native app. Additive only: every
route here is new; no existing HTML route/template/business logic is
touched. Mounted at /api/v1 in app/main.py."""
from fastapi import APIRouter

from . import (
    auth, tickets, tasks, home, employees, attendance, fms, sales, inventory,
    knowledge, devices, setup, setup_reference, setup_config, notifications,
    dashboard, checklists,
)

router = APIRouter(prefix="/api/v1")
router.include_router(auth.router)
router.include_router(tickets.router)
# checklists.router is registered before tasks.router: both define routes
# under /checklists/*, and tasks.router's GET /checklists/{assignment_id}
# is a single-segment param route that would otherwise swallow any
# single-segment literal path (e.g. checklists.router's /checklists/filter-
# options) registered after it — Starlette matches routes in registration
# order, first full match wins, regardless of literal-vs-param specificity.
router.include_router(checklists.router)
router.include_router(tasks.router)
router.include_router(home.router)
router.include_router(employees.router)
router.include_router(attendance.router)
router.include_router(fms.router)
router.include_router(sales.router)
router.include_router(inventory.router)
router.include_router(knowledge.router)
router.include_router(devices.router)
router.include_router(setup.router)
router.include_router(setup_reference.router)
router.include_router(setup_reference.customers_router)
router.include_router(setup_reference.vendors_router)
router.include_router(setup_reference.materials_router)
router.include_router(setup_reference.products_router)
router.include_router(setup_reference.uom_router)
router.include_router(setup_reference.lists_router)
router.include_router(setup_config.router)
router.include_router(notifications.router)
router.include_router(dashboard.router)
