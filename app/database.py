from sqlalchemy import create_engine, Column, String, Boolean, DateTime, Integer, Text, ForeignKey, Float, Date
from sqlalchemy.orm import declarative_base, sessionmaker, relationship
from datetime import datetime, date
import enum, uuid, os

# Use DATABASE_URL env var on Render (Postgres); fall back to local SQLite for dev.
DATABASE_URL = os.environ.get("DATABASE_URL")

if DATABASE_URL:
    # Render still issues legacy postgres:// URLs — SQLAlchemy requires postgresql://
    if DATABASE_URL.startswith("postgres://"):
        DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
    engine = create_engine(DATABASE_URL)
else:
    _PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _DB_FILE      = os.path.join(_PROJECT_ROOT, "omniflow.db")
    DATABASE_URL  = f"sqlite:///{_DB_FILE}"
    engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def new_id():
    return str(uuid.uuid4())

# ── Enums ────────────────────────────────────────────────────────────────────

class UserRole(str, enum.Enum):
    EMPLOYEE = "EMPLOYEE"
    MANAGER = "MANAGER"
    ADMIN = "ADMIN"

class TicketStatus(str, enum.Enum):
    OPEN = "OPEN"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    IN_PROGRESS = "IN_PROGRESS"
    HELP_REQUESTED = "HELP_REQUESTED"
    DONE = "DONE"
    CLOSED = "CLOSED"

class Priority(str, enum.Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"

class ChecklistStatus(str, enum.Enum):
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    DONE = "DONE"
    OVERDUE = "OVERDUE"

class ChecklistFrequency(str, enum.Enum):
    DAILY = "DAILY"
    WEEKLY = "WEEKLY"
    MONTHLY = "MONTHLY"
    PER_SHIFT = "PER_SHIFT"

# ── Models ────────────────────────────────────────────────────────────────────

class SuperAdmin(Base):
    """Platform-level super administrator — Phase 0-H"""
    __tablename__ = "super_admins"
    id           = Column(String,  primary_key=True, default=new_id)
    name         = Column(String,  nullable=False)
    email        = Column(String,  unique=True, nullable=False)
    password_hash= Column(String,  nullable=False)
    is_active    = Column(Boolean, default=True)
    last_login   = Column(DateTime)
    created_at   = Column(DateTime, default=datetime.utcnow)


class Tenant(Base):
    __tablename__ = "tenants"
    id            = Column(String,  primary_key=True, default=new_id)
    name          = Column(String,  nullable=False)
    slug          = Column(String,  unique=True, nullable=False)
    industry      = Column(String)
    plan          = Column(String,  default="STARTER")   # TRIAL / STARTER / PROFESSIONAL / ENTERPRISE
    contact_name  = Column(String)                        # Phase 0-H
    contact_email = Column(String)                        # Phase 0-H
    is_suspended  = Column(Boolean, default=False)        # Phase 0-H
    is_approved   = Column(Boolean, default=True)         # False for self-registered TRIAL tenants
    trial_started_at = Column(DateTime)                   # When self-registration happened
    ticket_seq    = Column(Integer,  default=0)                # Auto-increment counter for display IDs
    ai_custom_limit = Column(Integer, nullable=True)           # SA override for daily AI call limit (None = use plan default)
    created_at    = Column(DateTime, default=datetime.utcnow)

    users = relationship("User", back_populates="tenant")
    branches = relationship("Branch", back_populates="tenant")
    departments = relationship("Department", back_populates="tenant")
    tickets = relationship("Ticket", back_populates="tenant")
    checklist_templates = relationship("ChecklistTemplate", back_populates="tenant")

class Branch(Base):
    __tablename__ = "branches"
    id = Column(String, primary_key=True, default=new_id)
    tenant_id = Column(String, ForeignKey("tenants.id"), nullable=False)
    name = Column(String, nullable=False)
    address = Column(String)
    is_deleted = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    tenant = relationship("Tenant", back_populates="branches")
    departments = relationship("Department", back_populates="branch")

class Department(Base):
    __tablename__ = "departments"
    id = Column(String, primary_key=True, default=new_id)
    tenant_id = Column(String, ForeignKey("tenants.id"), nullable=False)
    branch_id = Column(String, ForeignKey("branches.id"), nullable=True)
    name = Column(String, nullable=False)
    is_deleted = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    tenant = relationship("Tenant", back_populates="departments")
    branch = relationship("Branch", back_populates="departments")
    users = relationship("User", back_populates="department")

class User(Base):
    __tablename__ = "users"
    id = Column(String, primary_key=True, default=new_id)
    tenant_id = Column(String, ForeignKey("tenants.id"), nullable=False)
    name = Column(String, nullable=False)
    phone = Column(String, nullable=False)
    email = Column(String)
    password_hash = Column(String, nullable=False)
    role = Column(String, default="EMPLOYEE")
    department_id = Column(String, ForeignKey("departments.id"))
    manager_id = Column(String, ForeignKey("users.id"))       # Phase 0-A-1
    is_active = Column(Boolean, default=True)
    is_deleted = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    tenant = relationship("Tenant", back_populates="users")
    department = relationship("Department", back_populates="users")
    manager = relationship("User", remote_side="User.id", foreign_keys="User.manager_id", backref="reports")
    created_tickets = relationship("Ticket", foreign_keys="Ticket.created_by_id", back_populates="created_by")
    assigned_tickets = relationship("Ticket", foreign_keys="Ticket.current_assignee_id", back_populates="current_assignee")

class Ticket(Base):
    __tablename__ = "tickets"
    id = Column(String, primary_key=True, default=new_id)
    display_id = Column(String, nullable=True)                  # e.g. T-0042
    tenant_id = Column(String, ForeignKey("tenants.id"), nullable=False)
    title = Column(String, nullable=False)
    description = Column(Text, nullable=False)
    priority = Column(String, default="MEDIUM")
    status = Column(String, default="OPEN")
    ticket_type = Column(String, default="D")               # D=Delegation, C=Checklist — Phase 0-C-4
    created_by_id = Column(String, ForeignKey("users.id"), nullable=False)
    current_assignee_id = Column(String, ForeignKey("users.id"))
    due_at = Column(DateTime)
    acknowledged_at = Column(DateTime)                       # Phase 0-G-3
    closed_at = Column(DateTime)
    is_flagged = Column(Boolean, default=False)
    flagged_reason = Column(String)
    proof_required = Column(Boolean, default=False)
    is_deleted = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    tenant = relationship("Tenant", back_populates="tickets")
    created_by = relationship("User", foreign_keys=[created_by_id], back_populates="created_tickets")
    current_assignee = relationship("User", foreign_keys=[current_assignee_id], back_populates="assigned_tickets")
    comments = relationship("TicketComment", back_populates="ticket", order_by="TicketComment.created_at")
    events = relationship("TicketEvent", back_populates="ticket", order_by="TicketEvent.created_at")
    helpers = relationship("TicketAssignee", back_populates="ticket", foreign_keys="TicketAssignee.ticket_id")
    media = relationship("MediaUpload", primaryjoin="and_(MediaUpload.entity_type=='ticket', foreign(MediaUpload.entity_id)==Ticket.id)", viewonly=True)

class TicketAssignee(Base):
    """Additional helpers on a ticket — Phase 0-C-1"""
    __tablename__ = "ticket_assignees"
    id = Column(String, primary_key=True, default=new_id)
    ticket_id = Column(String, ForeignKey("tickets.id"), nullable=False)
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    added_by_id = Column(String, ForeignKey("users.id"), nullable=False)
    note = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)

    ticket = relationship("Ticket", back_populates="helpers", foreign_keys=[ticket_id])
    user = relationship("User", foreign_keys=[user_id])
    added_by = relationship("User", foreign_keys=[added_by_id])

class TicketComment(Base):
    __tablename__ = "ticket_comments"
    id = Column(String, primary_key=True, default=new_id)
    ticket_id = Column(String, ForeignKey("tickets.id"), nullable=False)
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    body = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    ticket = relationship("Ticket", back_populates="comments")
    user = relationship("User")

class TicketEvent(Base):
    __tablename__ = "ticket_events"
    id = Column(String, primary_key=True, default=new_id)
    ticket_id = Column(String, ForeignKey("tickets.id"))
    actor_id = Column(String, ForeignKey("users.id"), nullable=False)
    event_type = Column(String, nullable=False)
    detail = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)

    ticket = relationship("Ticket", back_populates="events")
    actor = relationship("User")

class ChecklistTemplate(Base):
    __tablename__ = "checklist_templates"
    id = Column(String, primary_key=True, default=new_id)
    tenant_id = Column(String, ForeignKey("tenants.id"), nullable=False)
    title = Column(String, nullable=False)
    description = Column(Text, nullable=False)
    frequency = Column(String, default="DAILY")
    proof_required = Column(Boolean, default=False)
    assigned_to_role = Column(String)
    assigned_to_dept_id = Column(String, ForeignKey("departments.id"), nullable=True)
    assigned_to_user_id = Column(String, ForeignKey("users.id"), nullable=True)
    reminder_hours_before = Column(Integer, default=2)
    reminder_repeat_hours = Column(Integer, default=4)
    is_active = Column(Boolean, default=True)
    is_deleted = Column(Boolean, default=False)
    is_recurring = Column(Boolean, default=True)   # auto-schedule next on completion
    created_at = Column(DateTime, default=datetime.utcnow)

    tenant = relationship("Tenant", back_populates="checklist_templates")
    assignments = relationship("ChecklistAssignment", back_populates="template")
    assigned_to_user = relationship("User", foreign_keys=[assigned_to_user_id])
    assigned_to_dept = relationship("Department", foreign_keys=[assigned_to_dept_id])

class ChecklistAssignment(Base):
    __tablename__ = "checklist_assignments"
    id = Column(String, primary_key=True, default=new_id)
    template_id = Column(String, ForeignKey("checklist_templates.id"), nullable=False)
    tenant_id = Column(String, ForeignKey("tenants.id"), nullable=False)
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    due_at = Column(DateTime, nullable=False)
    completed_at = Column(DateTime)
    status = Column(String, default="PENDING")   # PENDING|IN_PROGRESS|OVERDUE|DONE|FAILED
    proof_url = Column(String)
    failure_note = Column(Text, nullable=True)    # reason when status=FAILED
    created_at = Column(DateTime, default=datetime.utcnow)

    template = relationship("ChecklistTemplate", back_populates="assignments")
    user = relationship("User")
    comments = relationship("ChecklistComment", back_populates="assignment")

class ChecklistComment(Base):
    __tablename__ = "checklist_comments"
    id = Column(String, primary_key=True, default=new_id)
    assignment_id = Column(String, ForeignKey("checklist_assignments.id"), nullable=False)
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    body = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    assignment = relationship("ChecklistAssignment", back_populates="comments")
    user = relationship("User")

class Notification(Base):
    """In-app notification centre — Phase 0-D-4"""
    __tablename__ = "notifications"
    id = Column(String, primary_key=True, default=new_id)
    tenant_id = Column(String, ForeignKey("tenants.id"), nullable=False)
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    notif_type = Column(String, nullable=False)
    title = Column(String, nullable=False)
    body = Column(Text)
    link = Column(String)
    is_read = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User")

class MediaUpload(Base):
    """Shared media table for tickets & checklists — Phase 0-E-1"""
    __tablename__ = "media_uploads"
    id = Column(String, primary_key=True, default=new_id)
    tenant_id = Column(String, ForeignKey("tenants.id"), nullable=False)
    entity_type = Column(String, nullable=False)   # "ticket" or "checklist"
    entity_id = Column(String, nullable=False)
    file_name = Column(String, nullable=False)
    file_path = Column(String, nullable=False)     # URL path under /static/uploads/
    file_type = Column(String)
    file_size = Column(Integer)
    uploaded_by_id = Column(String, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    uploaded_by = relationship("User")

class TenantLabelConfig(Base):
    """Per-tenant label customisation — Phase 0-J / Phase 4.
    Each concept stores a singular and plural label."""
    __tablename__ = "tenant_label_configs"
    id            = Column(String,  primary_key=True, default=new_id)
    tenant_id     = Column(String,  ForeignKey("tenants.id"), unique=True, nullable=False)
    # ── Core concepts ─────────────────────────────────────────────────────────
    ticket_s      = Column(String)   # e.g. "Work Order"
    ticket_p      = Column(String)   # e.g. "Work Orders"
    checklist_s   = Column(String)
    checklist_p   = Column(String)
    branch_s      = Column(String)
    branch_p      = Column(String)
    department_s  = Column(String)
    department_p  = Column(String)
    employee_s    = Column(String)
    employee_p    = Column(String)
    industry      = Column(String)   # which preset was last applied
    # ── Inventory concepts (Phase 4) ──────────────────────────────────────────
    inventory_s      = Column(String)   # "Inventory" / "Warehouse" / "Store"
    inventory_p      = Column(String)   # "Inventories" / "Warehouses" / "Stores"
    material_s       = Column(String)   # "Material" / "Product" / "Item" / "SKU"
    material_p       = Column(String)   # "Materials" / "Products" / "Items"
    stock_in_s       = Column(String)   # "Stock In" / "GRN" / "Receiving" / "Purchase Receipt"
    stock_out_s      = Column(String)   # "Stock Out" / "Issue" / "Consumption" / "Dispatch"
    adjustment_s     = Column(String)   # "Adjustment" / "Correction"
    purchase_order_s = Column(String)   # "Purchase Order" / "Procurement Order"
    purchase_order_p = Column(String)   # "Purchase Orders"
    supplier_s       = Column(String)   # "Supplier" / "Vendor" / "Provider"
    supplier_p       = Column(String)   # "Suppliers" / "Vendors"
    store_manager_s  = Column(String)   # "Store Manager" / "Warehouse Manager" / "Inventory Controller"
    store_manager_p  = Column(String)   # "Store Managers"
    updated_at    = Column(DateTime, default=datetime.utcnow)

    tenant = relationship("Tenant")


class TenantFeatureOverride(Base):
    """Per-tenant feature flag overrides — Phase 0-I.
    SA can force-enable or force-disable any feature regardless of plan."""
    __tablename__ = "tenant_feature_overrides"
    id         = Column(String,  primary_key=True, default=new_id)
    tenant_id  = Column(String,  ForeignKey("tenants.id"), nullable=False)
    feature    = Column(String,  nullable=False)
    enabled    = Column(Boolean, nullable=False)   # True = unlocked, False = locked
    note       = Column(String)                    # SA's internal note
    created_at = Column(DateTime, default=datetime.utcnow)

    tenant = relationship("Tenant")


class TenantAIUsage(Base):
    """Daily AI call counter per tenant — tracks Ask AI usage for limits and billing."""
    __tablename__ = "tenant_ai_usage"
    id         = Column(String,   primary_key=True, default=new_id)
    tenant_id  = Column(String,   ForeignKey("tenants.id"), nullable=False)
    date       = Column(String,   nullable=False)   # ISO date "YYYY-MM-DD"
    call_count = Column(Integer,  default=0)

    tenant = relationship("Tenant")


class PlanUpgradeRequest(Base):
    """Tenant-initiated plan upgrade request — surfaced in Super Admin portal."""
    __tablename__ = "plan_upgrade_requests"
    id          = Column(String,  primary_key=True, default=new_id)
    tenant_id   = Column(String,  ForeignKey("tenants.id"), nullable=False)
    from_plan   = Column(String,  nullable=False)
    to_plan     = Column(String,  nullable=False)
    message     = Column(String)
    status      = Column(String,  default="PENDING")   # PENDING / ACTIONED / DISMISSED
    created_at  = Column(DateTime, default=datetime.utcnow)
    actioned_at = Column(DateTime)
    actioned_by = Column(String,  ForeignKey("super_admins.id"))

    tenant = relationship("Tenant")


# ── Phase 0-K: Configuration Library Foundation ───────────────────────────────

class LibraryFlowTemplate(Base):
    """Named multi-stage flow definition (for FMS in Phase 2).
    Stored in the library; deployed to tenants via TenantDeployedItem."""
    __tablename__ = "library_flow_templates"
    id          = Column(String,  primary_key=True, default=new_id)
    name        = Column(String,  nullable=False)
    description = Column(Text)
    industry    = Column(String)
    version     = Column(Integer, default=1, nullable=False)
    status      = Column(String,  default="DRAFT")   # DRAFT / ACTIVE / DEPRECATED
    is_system   = Column(Boolean, default=False)     # built-in, cannot be deleted
    notes       = Column(Text)
    created_by  = Column(String,  ForeignKey("super_admins.id"))
    created_at  = Column(DateTime, default=datetime.utcnow)
    updated_at  = Column(DateTime, default=datetime.utcnow)

    stages = relationship("LibraryFlowStage",
                          back_populates="template",
                          order_by="LibraryFlowStage.order",
                          cascade="all, delete-orphan")


class LibraryFlowStage(Base):
    """One stage within a LibraryFlowTemplate."""
    __tablename__ = "library_flow_stages"
    id          = Column(String,  primary_key=True, default=new_id)
    template_id = Column(String,  ForeignKey("library_flow_templates.id"), nullable=False)
    name        = Column(String,  nullable=False)
    description = Column(Text)
    order       = Column(Integer, default=0)
    color       = Column(String,  default="#3b82f6")
    is_terminal = Column(Boolean, default=False)
    allowed_next_stages_json     = Column(Text,    default="[]")
    target_tat_hours             = Column(Integer, nullable=True)
    sub_module_tag               = Column(String,  nullable=True)   # PMS|DISPATCH|INVOICE|MATERIAL_REQ|CUSTOM
    submodule_id                 = Column(String,  ForeignKey("library_submodule_definitions.id"), nullable=True)
    completion_note_required     = Column(Boolean, default=False)

    template   = relationship("LibraryFlowTemplate", back_populates="stages")
    submodule  = relationship("LibrarySubmoduleDefinition", foreign_keys=[submodule_id])


class LibrarySubmoduleDefinition(Base):
    """A structured data-entry form definition (sub-module).
    sub_module_type: PMS | DISPATCH | INVOICE | MATERIAL_REQ | CUSTOM
    is_system=True  → built-in, cannot be edited directly (must duplicate)
    fields_json stores a JSON array of FieldDef objects:
    [{"id":"...", "label":"...", "type":"text|longtext|number|date|datetime|yesno|dropdown|photo|file|signature",
      "required": true, "options":["a","b"], "order":0}]
    """
    __tablename__ = "library_submodule_definitions"
    id               = Column(String,  primary_key=True, default=new_id)
    name             = Column(String,  nullable=False)
    description      = Column(Text)
    industry         = Column(String)
    sub_module_type  = Column(String,  default="CUSTOM")   # PMS|DISPATCH|INVOICE|MATERIAL_REQ|CUSTOM
    version          = Column(Integer, default=1, nullable=False)
    status           = Column(String,  default="DRAFT")
    is_system        = Column(Boolean, default=False)
    fields_json      = Column(Text,    nullable=False, default="[]")
    created_by       = Column(String,  ForeignKey("super_admins.id"))
    created_at       = Column(DateTime, default=datetime.utcnow)
    updated_at       = Column(DateTime, default=datetime.utcnow)


class LibraryChecklistTemplate(Base):
    """A checklist template stored in the library.
    Can be deployed (copied) into a tenant's checklist_templates."""
    __tablename__ = "library_checklist_templates"
    id          = Column(String,  primary_key=True, default=new_id)
    name        = Column(String,  nullable=False)
    description = Column(Text)
    frequency   = Column(String,  default="DAILY")
    industry    = Column(String)
    version     = Column(Integer, default=1, nullable=False)
    status      = Column(String,  default="DRAFT")
    is_system   = Column(Boolean, default=False)
    proof_required = Column(Boolean, default=False)
    assigned_to_role = Column(String, default="EMPLOYEE")
    notes       = Column(Text)
    created_by  = Column(String,  ForeignKey("super_admins.id"))
    created_at  = Column(DateTime, default=datetime.utcnow)
    updated_at  = Column(DateTime, default=datetime.utcnow)


class LibraryLabelBundle(Base):
    """A named set of label overrides (vocabulary preset).
    Applying a bundle writes to TenantLabelConfig."""
    __tablename__ = "library_label_bundles"
    id          = Column(String,  primary_key=True, default=new_id)
    name        = Column(String,  nullable=False)
    description = Column(Text)
    industry    = Column(String)
    version     = Column(Integer, default=1, nullable=False)
    is_system   = Column(Boolean, default=True)
    ticket_s    = Column(String)
    ticket_p    = Column(String)
    checklist_s = Column(String)
    checklist_p = Column(String)
    branch_s    = Column(String)
    branch_p    = Column(String)
    department_s= Column(String)
    department_p= Column(String)
    employee_s  = Column(String)
    employee_p  = Column(String)
    created_at  = Column(DateTime, default=datetime.utcnow)
    updated_at  = Column(DateTime, default=datetime.utcnow)


class LibraryOnboardingBundle(Base):
    """A full tenant startup package: labels + checklists + flows + settings.
    Deploying a bundle provisions everything in one click at tenant creation."""
    __tablename__ = "library_onboarding_bundles"
    id                     = Column(String,  primary_key=True, default=new_id)
    name                   = Column(String,  nullable=False)
    description            = Column(Text)
    industry               = Column(String)
    version                = Column(Integer, default=1, nullable=False)
    is_system              = Column(Boolean, default=True)
    label_bundle_id        = Column(String,  ForeignKey("library_label_bundles.id"), nullable=True)
    checklist_ids_json     = Column(Text,    default="[]")   # [library_checklist_template.id, ...]
    flow_template_ids_json = Column(Text,    default="[]")
    submodule_ids_json     = Column(Text,    default="[]")
    notes                  = Column(Text)
    created_at             = Column(DateTime, default=datetime.utcnow)
    updated_at             = Column(DateTime, default=datetime.utcnow)

    label_bundle = relationship("LibraryLabelBundle")


class TenantDeployedItem(Base):
    """Tracks which version of each library item has been deployed to which tenant.
    Used for: update-available indicators, diff views, bulk-push."""
    __tablename__ = "tenant_deployed_items"
    id               = Column(String,  primary_key=True, default=new_id)
    tenant_id        = Column(String,  ForeignKey("tenants.id"), nullable=False)
    item_type        = Column(String,  nullable=False)
    # "flow" | "submodule" | "checklist" | "label_bundle" | "onboarding_bundle"
    library_item_id  = Column(String,  nullable=False)
    item_name        = Column(String)    # snapshot of name at deploy time
    deployed_version = Column(Integer,  nullable=False, default=1)
    # latest_version is the library version at time of deploy — used for diff
    deployed_at      = Column(DateTime, default=datetime.utcnow)
    deployed_by      = Column(String,   ForeignKey("super_admins.id"))
    notes            = Column(Text)

    tenant = relationship("Tenant")


class WebSocketSession(Base):
    """
    Active WebSocket connection tracking — Phase 1-4.
    One row per live connection; deleted on disconnect.
    Used for targeted broadcasts (manager sees team events only).
    """
    __tablename__ = "websocket_sessions"
    id           = Column(String,  primary_key=True, default=new_id)
    tenant_id    = Column(String,  ForeignKey("tenants.id"), nullable=False)
    user_id      = Column(String,  ForeignKey("users.id"),   nullable=False)
    connected_at = Column(DateTime, default=datetime.utcnow)
    last_ping    = Column(DateTime)
    user_agent   = Column(String)

    user   = relationship("User")
    tenant = relationship("Tenant")


# ── Phase 2: FMS Core ─────────────────────────────────────────────────────────

class FMSFlow(Base):
    """
    A named, multi-stage process flow owned by a tenant.
    Can be created manually or deployed from a LibraryFlowTemplate (2-B-5).
    """
    __tablename__ = "fms_flows"
    id                       = Column(String,  primary_key=True, default=new_id)
    tenant_id                = Column(String,  ForeignKey("tenants.id"), nullable=False)
    name                     = Column(String,  nullable=False)
    description              = Column(Text)
    color                    = Column(String,  default="#3b82f6")   # swimlane colour
    is_active                = Column(Boolean, default=True)
    is_deleted               = Column(Boolean, default=False)
    library_flow_id          = Column(String)                        # source library template (if any)
    library_version_at_deploy= Column(Integer)                       # version at time of deploy (2-B-5)
    created_by_id            = Column(String,  ForeignKey("users.id"))
    created_at               = Column(DateTime, default=datetime.utcnow)
    updated_at               = Column(DateTime, default=datetime.utcnow)

    tenant     = relationship("Tenant")
    created_by = relationship("User", foreign_keys=[created_by_id])
    stages     = relationship("FMSStage",  back_populates="flow",
                              order_by="FMSStage.order",
                              cascade="all, delete-orphan")
    tickets    = relationship("FMSTicket", back_populates="flow")


class FMSStage(Base):
    """
    One stage inside an FMSFlow.
    Fields per §10.2: name, order, target_tat_hours, default_assignee,
    sub_module_tag, is_mandatory, completion_note_required, is_terminal.
    sub_module_tag values: None | PMS | DISPATCH | INVOICE | MATERIAL_REQ | CUSTOM
    """
    __tablename__ = "fms_stages"
    id                      = Column(String,  primary_key=True, default=new_id)
    flow_id                 = Column(String,  ForeignKey("fms_flows.id"), nullable=False)
    tenant_id               = Column(String,  ForeignKey("tenants.id"), nullable=False)
    name                    = Column(String,  nullable=False)
    order                   = Column(Integer, default=0)
    color                   = Column(String,  default="#3b82f6")
    target_tat_hours        = Column(Integer, nullable=True)         # None = no TaT target
    default_assignee_id     = Column(String,  ForeignKey("users.id"), nullable=True)
    sub_module_tag          = Column(String,  nullable=True)         # PMS|DISPATCH|INVOICE|MATERIAL_REQ|CUSTOM
    deployed_submodule_id   = Column(String,  ForeignKey("library_submodule_definitions.id"), nullable=True)
    is_mandatory            = Column(Boolean, default=True)
    completion_note_required= Column(Boolean, default=False)
    is_terminal             = Column(Boolean, default=False)         # reaching this = COMPLETED
    is_deleted              = Column(Boolean, default=False)

    flow              = relationship("FMSFlow", back_populates="stages")
    default_assignee  = relationship("User", foreign_keys=[default_assignee_id])
    tenant            = relationship("Tenant")
    deployed_submodule= relationship("LibrarySubmoduleDefinition", foreign_keys=[deployed_submodule_id])


class FMSTicket(Base):
    """
    An FMS work-order ticket that moves through the stages of an FMSFlow.

    Status values (§11.6):
      ACTIVE          – being worked at current stage
      STAGE_COMPLETE  – assignee marked stage done, pending transition
      IN_TRANSITION   – mid-move (brief; set/cleared by transition handler)
      HELP_REQUESTED  – assignee asked for help
      FLAGGED         – manager flagged for attention
      ON_HOLD         – paused by admin/manager
      COMPLETED       – terminal stage reached
      CLOSED          – administratively closed
    """
    __tablename__ = "fms_tickets"
    id                  = Column(String,  primary_key=True, default=new_id)
    display_id          = Column(String,  nullable=True)        # e.g. F-0042
    tenant_id           = Column(String,  ForeignKey("tenants.id"), nullable=False)
    flow_id             = Column(String,  ForeignKey("fms_flows.id"), nullable=False)
    current_stage_id    = Column(String,  ForeignKey("fms_stages.id"), nullable=True)
    title               = Column(String,  nullable=False)
    description         = Column(Text)
    wo_number           = Column(String)
    priority            = Column(String,  default="MEDIUM")
    status              = Column(String,  default="ACTIVE")
    target_qty          = Column(Integer, nullable=True)
    qty_unit            = Column(String,  nullable=True)
    current_assignee_id = Column(String,  ForeignKey("users.id"), nullable=True)
    due_at              = Column(DateTime, nullable=True)
    is_flagged          = Column(Boolean, default=False)
    flagged_reason      = Column(String)
    completed_at        = Column(DateTime)
    closed_at           = Column(DateTime)
    is_deleted          = Column(Boolean, default=False)
    created_by_id       = Column(String,  ForeignKey("users.id"), nullable=False)
    created_at          = Column(DateTime, default=datetime.utcnow)
    updated_at          = Column(DateTime, default=datetime.utcnow)

    flow             = relationship("FMSFlow", back_populates="tickets")
    current_stage    = relationship("FMSStage",  foreign_keys=[current_stage_id])
    current_assignee = relationship("User",       foreign_keys=[current_assignee_id])
    created_by       = relationship("User",       foreign_keys=[created_by_id])
    tenant           = relationship("Tenant")
    stage_history    = relationship("FMSStageHistory", back_populates="ticket",
                                    order_by="FMSStageHistory.entered_at",
                                    cascade="all, delete-orphan")
    events           = relationship("FMSEvent", back_populates="ticket",
                                    order_by="FMSEvent.created_at",
                                    cascade="all, delete-orphan")
    helpers          = relationship("FMSTicketHelper", back_populates="ticket",
                                    foreign_keys="FMSTicketHelper.ticket_id",
                                    cascade="all, delete-orphan")


class FMSStageHistory(Base):
    """
    Immutable log of every stage visit — §11, 2-C-6.
    NEVER updated after creation; revisits create NEW rows (non-linear, 2-C-5).
    exited_at=None means this is the currently active stage visit.
    """
    __tablename__ = "fms_stage_history"
    id               = Column(String,  primary_key=True, default=new_id)
    ticket_id        = Column(String,  ForeignKey("fms_tickets.id"), nullable=False)
    stage_id         = Column(String,  ForeignKey("fms_stages.id"), nullable=False)
    stage_name       = Column(String)           # name snapshot at time of entry
    assignee_id      = Column(String,  ForeignKey("users.id"), nullable=True)
    entered_at       = Column(DateTime, default=datetime.utcnow, nullable=False)
    exited_at        = Column(DateTime, nullable=True)   # None = still active here
    direction        = Column(String,  default="FORWARD")
    # FORWARD | BACKWARD | MANAGER_OVERRIDE
    return_reason    = Column(Text)             # required for BACKWARD / MANAGER_OVERRIDE
    completion_note  = Column(Text)             # what was done at this stage
    qty_completed    = Column(Integer, default=0)
    from_stage_id    = Column(String,  nullable=True)
    from_stage_name  = Column(String,  nullable=True)

    ticket   = relationship("FMSTicket", back_populates="stage_history")
    stage    = relationship("FMSStage",  foreign_keys=[stage_id])
    assignee = relationship("User",      foreign_keys=[assignee_id])


class FMSEvent(Base):
    """
    Immutable, append-only audit log for all events on an FMS ticket.
    All event types for all phases defined here so the log stays coherent
    as Phase 3/4 features add new event types.
    """
    __tablename__ = "fms_events"
    id         = Column(String,  primary_key=True, default=new_id)
    ticket_id  = Column(String,  ForeignKey("fms_tickets.id"), nullable=False)
    actor_id   = Column(String,  ForeignKey("users.id"), nullable=True)
    event_type = Column(String,  nullable=False)
    # Phase 2: CREATED STAGE_ENTERED STAGE_EXITED RETURNED REASSIGNED
    #          HELP_REQUESTED HELPER_ADDED HELPER_REMOVED FLAGGED UNFLAGGED
    #          COMPLETED CLOSED MANAGER_OVERRIDE COMMENT ON_HOLD RESUMED
    # Phase 3: PMS_ENTRY DISPATCH_ENTRY INVOICE_ENTRY TARGET_REVISED
    detail     = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)

    ticket = relationship("FMSTicket", back_populates="events")
    actor  = relationship("User",      foreign_keys=[actor_id])


class FMSTicketHelper(Base):
    """
    Additional helpers on an FMS ticket — reuses Phase 0-C pattern (2-D-3).
    """
    __tablename__ = "fms_ticket_helpers"
    id          = Column(String,  primary_key=True, default=new_id)
    ticket_id   = Column(String,  ForeignKey("fms_tickets.id"), nullable=False)
    user_id     = Column(String,  ForeignKey("users.id"), nullable=False)
    added_by_id = Column(String,  ForeignKey("users.id"), nullable=False)
    reason      = Column(Text)
    note        = Column(Text)
    created_at  = Column(DateTime, default=datetime.utcnow)

    ticket    = relationship("FMSTicket", back_populates="helpers", foreign_keys=[ticket_id])
    user      = relationship("User", foreign_keys=[user_id])
    added_by  = relationship("User", foreign_keys=[added_by_id])


# ═══════════════════════════════════════════════════════════════════
# Phase 3 — Sub-module Tables
# ═══════════════════════════════════════════════════════════════════

class PMSDailyLog(Base):
    """
    Phase 3-A: Immutable daily production log per FMS ticket.
    event_type: DAILY_LOG | NO_ENTRY | TARGET_REVISED
    """
    __tablename__ = "pms_daily_logs"
    id              = Column(String,  primary_key=True, default=new_id)
    ticket_id       = Column(String,  ForeignKey("fms_tickets.id"), nullable=False)
    tenant_id       = Column(String,  ForeignKey("tenants.id"),     nullable=False)
    log_date        = Column(Date,    nullable=False)
    qty_done        = Column(Integer, default=0)
    has_blockers    = Column(Boolean, default=False)
    comment         = Column(Text)
    event_type      = Column(String,  default="DAILY_LOG")   # DAILY_LOG | NO_ENTRY | TARGET_REVISED
    old_target      = Column(Integer, nullable=True)          # TARGET_REVISED only
    new_target      = Column(Integer, nullable=True)          # TARGET_REVISED only
    revision_reason = Column(Text)
    actor_id        = Column(String,  ForeignKey("users.id"), nullable=True)
    created_at      = Column(DateTime, default=datetime.utcnow)
    # immutable — no is_deleted

    ticket = relationship("FMSTicket", foreign_keys=[ticket_id])
    actor  = relationship("User",      foreign_keys=[actor_id])


class DispatchRecord(Base):
    """
    Phase 3-B: Immutable dispatch entry per FMS ticket.
    Multiple records per ticket = partial dispatch support.
    """
    __tablename__ = "dispatch_records"
    id                = Column(String,   primary_key=True, default=new_id)
    ticket_id         = Column(String,   ForeignKey("fms_tickets.id"), nullable=False)
    tenant_id         = Column(String,   ForeignKey("tenants.id"),     nullable=False)
    qty_dispatched    = Column(Integer,  nullable=False)
    unit              = Column(String)
    vehicle_number    = Column(String)
    driver_name       = Column(String)
    destination       = Column(String)
    expected_delivery = Column(DateTime, nullable=True)
    notes             = Column(Text)
    proof_photo_url   = Column(String)           # POD (Proof of Dispatch)
    pod_uploaded_at   = Column(DateTime, nullable=True)
    is_delivered      = Column(Boolean,  default=False)
    delivered_at      = Column(DateTime, nullable=True)
    actor_id          = Column(String,   ForeignKey("users.id"), nullable=True)
    created_at        = Column(DateTime, default=datetime.utcnow)
    # immutable

    ticket = relationship("FMSTicket", foreign_keys=[ticket_id])
    actor  = relationship("User",      foreign_keys=[actor_id])


class InvoiceRecord(Base):
    """
    Phase 3-C: Invoice linked to an FMS ticket.
    Multiple per ticket for advance + final billing.
    """
    __tablename__ = "invoice_records"
    id             = Column(String,  primary_key=True, default=new_id)
    ticket_id      = Column(String,  ForeignKey("fms_tickets.id"), nullable=False)
    tenant_id      = Column(String,  ForeignKey("tenants.id"),     nullable=False)
    invoice_number = Column(String,  nullable=False)
    amount         = Column(Float,   nullable=False)
    currency       = Column(String,  default="INR")
    invoice_date   = Column(Date,    nullable=True)
    due_date       = Column(Date,    nullable=True)
    payment_terms  = Column(String)
    is_paid        = Column(Boolean, default=False)
    paid_at        = Column(DateTime, nullable=True)
    payment_ref    = Column(String)
    document_url   = Column(String)
    is_deleted     = Column(Boolean, default=False)
    actor_id       = Column(String,  ForeignKey("users.id"), nullable=True)
    created_at     = Column(DateTime, default=datetime.utcnow)
    updated_at     = Column(DateTime, default=datetime.utcnow)

    ticket = relationship("FMSTicket", foreign_keys=[ticket_id])
    actor  = relationship("User",      foreign_keys=[actor_id])


class Material(Base):
    """
    Phase 3-D / 4-B: Raw material catalogue entry per tenant.
    Phase 3 creates the table; Phase 4 adds inventory movement tracking.
    """
    __tablename__ = "materials"
    id                = Column(String,   primary_key=True, default=new_id)
    tenant_id         = Column(String,   ForeignKey("tenants.id"), nullable=False)
    name              = Column(String,   nullable=False)
    unit              = Column(String,   default="pcs")
    description       = Column(Text)
    reorder_threshold = Column(Integer,  default=0)
    reorder_qty       = Column(Integer,  default=0)
    lead_time_days    = Column(Integer,  default=0)
    supplier          = Column(String)
    opening_stock     = Column(Integer,  default=0)
    current_stock     = Column(Integer,  default=0)
    is_active         = Column(Boolean,  default=True)
    is_deleted        = Column(Boolean,  default=False)
    created_by_id     = Column(String,   ForeignKey("users.id"), nullable=True)
    created_at        = Column(DateTime, default=datetime.utcnow)
    updated_at        = Column(DateTime, default=datetime.utcnow)

    created_by = relationship("User", foreign_keys=[created_by_id])
    tenant     = relationship("Tenant")


class MaterialRequest(Base):
    """
    Phase 3-D: Material requisition raised from an FMS ticket.
    Store Manager approval flow is completed in Phase 4.
    status: PENDING | APPROVED | REJECTED | FULFILLED
    """
    __tablename__ = "material_requests"
    id               = Column(String,  primary_key=True, default=new_id)
    ticket_id        = Column(String,  ForeignKey("fms_tickets.id"), nullable=True)
    tenant_id        = Column(String,  ForeignKey("tenants.id"),     nullable=False)
    material_id      = Column(String,  ForeignKey("materials.id"),   nullable=True)
    material_name    = Column(String)                                 # text fallback
    qty_requested    = Column(Integer, nullable=False)
    unit             = Column(String)
    reason           = Column(Text)
    status           = Column(String,  default="PENDING")
    approved_by_id   = Column(String,  ForeignKey("users.id"), nullable=True)
    approved_at      = Column(DateTime, nullable=True)
    rejection_note   = Column(Text)
    fulfilled_qty    = Column(Integer, default=0)
    fulfilled_at     = Column(DateTime, nullable=True)
    requested_by_id  = Column(String,  ForeignKey("users.id"), nullable=False)
    created_at       = Column(DateTime, default=datetime.utcnow)
    updated_at       = Column(DateTime, default=datetime.utcnow)

    stage_id         = Column(String,  ForeignKey("fms_stages.id"), nullable=True)
    stage_name       = Column(String,  nullable=True)   # snapshot at time of request

    ticket       = relationship("FMSTicket",  foreign_keys=[ticket_id])
    material     = relationship("Material",   foreign_keys=[material_id])
    requested_by = relationship("User",       foreign_keys=[requested_by_id])
    approved_by  = relationship("User",       foreign_keys=[approved_by_id])
    stage        = relationship("FMSStage",   foreign_keys=[stage_id])


# ── Phase 4: Inventory Management ─────────────────────────────────────────────

class StockMovement(Base):
    """
    Phase 4-C: Immutable ledger of every stock change.
    movement_type: STOCK_IN | STOCK_OUT | ADJUSTMENT | PO_RECEIPT | OPENING | RETURN
    qty is always positive; direction is encoded in movement_type.
    qty_before / qty_after are snapshots for full auditability.
    """
    __tablename__ = "stock_movements"
    id            = Column(String,   primary_key=True, default=new_id)
    tenant_id     = Column(String,   ForeignKey("tenants.id"),   nullable=False)
    material_id   = Column(String,   ForeignKey("materials.id"), nullable=False)
    branch_id     = Column(String,   ForeignKey("branches.id"),  nullable=True)
    department_id = Column(String,   ForeignKey("departments.id"), nullable=True)
    movement_type = Column(String,   nullable=False)
    qty           = Column(Float,    nullable=False)      # always positive
    qty_before    = Column(Float,    nullable=False, default=0)
    qty_after     = Column(Float,    nullable=False, default=0)
    unit          = Column(String)
    unit_cost     = Column(Float,    nullable=True)       # cost per unit at time of movement
    total_cost    = Column(Float,    nullable=True)
    reference     = Column(String)                        # PO number, ticket id, batch no.
    notes         = Column(Text)
    po_item_id    = Column(String,   ForeignKey("purchase_order_items.id"), nullable=True)
    actor_id      = Column(String,   ForeignKey("users.id"), nullable=False)
    created_at    = Column(DateTime, default=datetime.utcnow)
    # immutable — no is_deleted, no updated_at

    material   = relationship("Material",          foreign_keys=[material_id])
    actor      = relationship("User",              foreign_keys=[actor_id])
    tenant     = relationship("Tenant")
    po_item    = relationship("PurchaseOrderItem", foreign_keys=[po_item_id])


class PurchaseOrder(Base):
    """
    Phase 4-D: Purchase Order header.
    status: DRAFT → SUBMITTED → APPROVED → PARTIALLY_RECEIVED → RECEIVED → CANCELLED
    """
    __tablename__ = "purchase_orders"
    id               = Column(String,   primary_key=True, default=new_id)
    tenant_id        = Column(String,   ForeignKey("tenants.id"),    nullable=False)
    branch_id        = Column(String,   ForeignKey("branches.id"),   nullable=True)
    department_id    = Column(String,   ForeignKey("departments.id"), nullable=True)
    po_number        = Column(String,   nullable=False)   # auto-generated or manual
    supplier         = Column(String)
    supplier_ref     = Column(String)                     # supplier's own order/invoice ref
    status           = Column(String,   default="DRAFT")
    expected_delivery= Column(Date,     nullable=True)
    notes            = Column(Text)
    total_amount     = Column(Float,    default=0)
    currency         = Column(String,   default="INR")
    is_deleted       = Column(Boolean,  default=False)
    created_by_id    = Column(String,   ForeignKey("users.id"), nullable=False)
    approved_by_id   = Column(String,   ForeignKey("users.id"), nullable=True)
    approved_at      = Column(DateTime, nullable=True)
    received_at      = Column(DateTime, nullable=True)
    cancelled_at     = Column(DateTime, nullable=True)
    cancel_reason    = Column(String)
    created_at       = Column(DateTime, default=datetime.utcnow)
    updated_at       = Column(DateTime, default=datetime.utcnow)

    tenant      = relationship("Tenant")
    created_by  = relationship("User", foreign_keys=[created_by_id])
    approved_by = relationship("User", foreign_keys=[approved_by_id])
    items       = relationship("PurchaseOrderItem", back_populates="po",
                               cascade="all, delete-orphan")


class PurchaseOrderItem(Base):
    """Phase 4-D: Line items on a Purchase Order."""
    __tablename__ = "purchase_order_items"
    id               = Column(String,  primary_key=True, default=new_id)
    po_id            = Column(String,  ForeignKey("purchase_orders.id"), nullable=False)
    tenant_id        = Column(String,  ForeignKey("tenants.id"),         nullable=False)
    material_id      = Column(String,  ForeignKey("materials.id"),       nullable=True)
    material_name    = Column(String,  nullable=False)    # name snapshot at PO creation time
    unit             = Column(String)
    qty_ordered      = Column(Float,   nullable=False)
    qty_received     = Column(Float,   default=0)
    unit_cost        = Column(Float,   nullable=True)
    total_cost       = Column(Float,   nullable=True)
    is_fully_received= Column(Boolean, default=False)

    po       = relationship("PurchaseOrder", back_populates="items")
    material = relationship("Material",      foreign_keys=[material_id])
    movements= relationship("StockMovement", back_populates="po_item")


class CustomSubmoduleResponse(Base):
    """
    Phase 3-E: Flexible JSON response for a custom sub-module on an FMS stage.
    field_responses_json stores {field_id: serialised_value}.
    """
    __tablename__ = "custom_submodule_responses"
    id                   = Column(String,  primary_key=True, default=new_id)
    ticket_id            = Column(String,  ForeignKey("fms_tickets.id"), nullable=False)
    stage_id             = Column(String,  ForeignKey("fms_stages.id"),  nullable=False)
    tenant_id            = Column(String,  ForeignKey("tenants.id"),     nullable=False)
    submodule_def_id     = Column(String,  ForeignKey("library_submodule_definitions.id"), nullable=False)
    field_responses_json = Column(Text,    default="{}")
    is_complete          = Column(Boolean, default=False)
    submitted_at         = Column(DateTime, nullable=True)
    actor_id             = Column(String,  ForeignKey("users.id"), nullable=True)
    created_at           = Column(DateTime, default=datetime.utcnow)
    updated_at           = Column(DateTime, default=datetime.utcnow)

    ticket        = relationship("FMSTicket",                foreign_keys=[ticket_id])
    stage         = relationship("FMSStage",                 foreign_keys=[stage_id])
    submodule_def = relationship("LibrarySubmoduleDefinition", foreign_keys=[submodule_def_id])
    actor         = relationship("User",                     foreign_keys=[actor_id])


def create_tables():
    Base.metadata.create_all(bind=engine)
    # Auto-migrate: add any columns present in models but missing from the DB
    try:
        import sys, os
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from migrate import run_migrations
        run_migrations()
    except Exception as _e:
        import logging
        logging.getLogger(__name__).warning("migrate.py skipped: %s", _e)
    _seed_builtin_submodules()


# Fixed IDs so they survive re-seeds (never change these)
_BUILTIN_SUBMODULES = [
    {
        "id": "sys-pms-builtin",
        "name": "Production Log (PMS)",
        "sub_module_type": "PMS",
        "description": (
            "Daily production quantity logging with blocker tracking and target revision. "
            "Generates a cumulative progress bar on the ticket."
        ),
        "fields_json": "[]",   # built-in logic — no dynamic fields needed
    },
    {
        "id": "sys-dispatch-builtin",
        "name": "Dispatch & Delivery",
        "sub_module_type": "DISPATCH",
        "description": (
            "Shipment records with vehicle/driver info, expected delivery date, "
            "and proof-of-delivery (POD) photo upload."
        ),
        "fields_json": "[]",
    },
    {
        "id": "sys-invoice-builtin",
        "name": "Invoice Management",
        "sub_module_type": "INVOICE",
        "description": (
            "Raise invoices against a ticket, track due dates, "
            "and mark payments received with reference numbers."
        ),
        "fields_json": "[]",
    },
    {
        "id": "sys-material-builtin",
        "name": "Material Requisition",
        "sub_module_type": "MATERIAL_REQ",
        "description": (
            "Request raw materials from the tenant catalogue. "
            "Manager approves or rejects each request with notes."
        ),
        "fields_json": "[]",
    },
]


def _seed_builtin_submodules():
    """Ensure the 4 built-in system sub-module records exist in the library.
    Safe to call on every startup — uses upsert logic."""
    db = SessionLocal()
    try:
        for defn in _BUILTIN_SUBMODULES:
            existing = db.query(LibrarySubmoduleDefinition).filter(
                LibrarySubmoduleDefinition.id == defn["id"]
            ).first()
            if existing:
                # Refresh description / name in case we updated them above
                existing.name            = defn["name"]
                existing.description     = defn["description"]
                existing.sub_module_type = defn["sub_module_type"]
                existing.status          = "PUBLISHED"
                existing.is_system       = True
            else:
                db.add(LibrarySubmoduleDefinition(
                    id              = defn["id"],
                    name            = defn["name"],
                    description     = defn["description"],
                    sub_module_type = defn["sub_module_type"],
                    status          = "PUBLISHED",
                    is_system       = True,
                    fields_json     = defn["fields_json"],
                    version         = 1,
                ))
        db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()
