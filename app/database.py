from sqlalchemy import create_engine, Column, String, Boolean, DateTime, Integer, Text, ForeignKey, Float, Date, JSON, UniqueConstraint
from sqlalchemy.orm import declarative_base, sessionmaker, relationship
from datetime import datetime, date
import enum, uuid, os

# Use DATABASE_URL env var on Render (Postgres); fall back to local SQLite for dev.
DATABASE_URL = os.environ.get("DATABASE_URL")

if DATABASE_URL:
    # Render still issues legacy postgres:// URLs — SQLAlchemy requires postgresql://
    if DATABASE_URL.startswith("postgres://"):
        DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
    # pool_pre_ping: validates connections before use (handles Render idle-timeout drops)
    engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_size=5, max_overflow=10)
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

# ── Gupshup WhatsApp multi-tenant migration enums ─────────────────────────────

class WabaStatus(str, enum.Enum):
    PENDING = "PENDING"
    LIVE = "LIVE"
    FLAGGED = "FLAGGED"
    SUSPENDED = "SUSPENDED"

class OptInStatus(str, enum.Enum):
    PENDING = "PENDING"
    OPTED_IN = "OPTED_IN"
    MISMATCH = "MISMATCH"
    MANUALLY_VERIFIED = "MANUALLY_VERIFIED"
    OPTED_OUT = "OPTED_OUT"

class OptInSource(str, enum.Enum):
    QR = "QR"
    MANUAL_OVERRIDE = "MANUAL_OVERRIDE"
    EXISTING_THREAD = "EXISTING_THREAD"

class ConsentEventType(str, enum.Enum):
    OPT_IN_RECEIVED = "OPT_IN_RECEIVED"
    MARKED_MISMATCH = "MARKED_MISMATCH"
    PHONE_CORRECTED = "PHONE_CORRECTED"
    MANUAL_OVERRIDE = "MANUAL_OVERRIDE"
    OPT_OUT_RECEIVED = "OPT_OUT_RECEIVED"

class TemplateCategory(str, enum.Enum):
    UTILITY = "UTILITY"
    MARKETING = "MARKETING"
    AUTHENTICATION = "AUTHENTICATION"

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
    checklist_notif_hours = Column(String, nullable=True)      # Comma-separated UTC hours for checklist notifications e.g. "8,13,18"
    checklist_overdue_hour = Column(String, nullable=True)     # Single IST hour for daily overdue WhatsApp e.g. "19". NULL = disabled.
    # E-15: Office hours
    work_start_time           = Column(String,  nullable=True, default='09:00')
    work_end_time             = Column(String,  nullable=True, default='18:00')
    work_days                 = Column(String,  nullable=True, default='0,1,2,3,4')
    timezone                  = Column(String,  nullable=True, default='Asia/Kolkata')
    suppress_notif_outside_hours = Column(Boolean, default=False)
    # E-15: Checklist notification defaults
    checklist_remind_before_hours = Column(Integer, default=2)
    checklist_remind_repeat_hours = Column(Integer, default=4)
    checklist_notif_on_assign     = Column(Boolean, default=True)
    # E-15: Delegation ticket notification settings
    ticket_notif_on_assign    = Column(Boolean, default=True)
    ticket_notif_unack_hours  = Column(Integer, default=4)
    ticket_notif_tat_pct      = Column(Integer, default=80)
    ticket_notif_tat_pct_both = Column(Integer, default=90)
    # E-15: FMS notification settings
    fms_notif_on_open         = Column(Boolean, default=True)
    fms_notif_on_stage_entry  = Column(Boolean, default=True)
    fms_notif_tat_pct         = Column(Integer, default=80)
    fms_notif_on_backward     = Column(Boolean, default=True)
    fms_notif_on_flag         = Column(Boolean, default=True)
    created_at    = Column(DateTime, default=datetime.utcnow)
    # Gupshup WhatsApp — per-tenant WABA configuration
    gupshup_client_id       = Column(String, nullable=True)
    gupshup_secret_token    = Column(String, nullable=True)
    gupshup_source_number   = Column(String, nullable=True)   # E.164
    gupshup_waba_status     = Column(String, nullable=True, default=WabaStatus.PENDING.value)
    gupshup_webhook_token   = Column(String, unique=True, nullable=True)
    gupshup_webhook_secret  = Column(String, nullable=True)
    whatsapp_opt_in_link    = Column(String, nullable=True)
    whatsapp_config_updated_at = Column(DateTime, nullable=True)
    # Per-event WhatsApp channel toggles (Setup > Notifications) — gates the
    # WhatsApp send for each pipeline independently of the in-app notification
    # toggles above. Default True preserves today's always-on behavior.
    wa_notif_ticket_assigned     = Column(Boolean, default=True)
    wa_notif_ticket_escalated    = Column(Boolean, default=True)
    wa_notif_fms_ticket_created  = Column(Boolean, default=True)
    wa_notif_fms_stage_transition = Column(Boolean, default=True)
    wa_notif_order_placed        = Column(Boolean, default=True)
    wa_notif_order_dispatched    = Column(Boolean, default=True)
    wa_notif_ticket_closed        = Column(Boolean, default=True)
    wa_notif_ticket_tat_reminder  = Column(Boolean, default=True)
    wa_notif_fms_ticket_closed    = Column(Boolean, default=True)
    wa_notif_fms_ticket_flagged   = Column(Boolean, default=True)
    wa_notif_po_placed            = Column(Boolean, default=True)
    wa_notif_po_accepted          = Column(Boolean, default=True)

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
    branch_id = Column(String, ForeignKey("branches.id"), nullable=True)   # P1-09
    manager_id = Column(String, ForeignKey("users.id"))       # Phase 0-A-1
    is_active = Column(Boolean, default=True)
    is_deleted = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    # P1-05: Employee profile additions
    employee_id   = Column(String)          # auto-generated EMP-XXXX per tenant
    joining_date  = Column(Date)
    address       = Column(Text)
    status        = Column(String, default="ACTIVE")   # ACTIVE / TERMINATED
    terminated_at = Column(DateTime)
    last_login    = Column(DateTime)
    # WhatsApp validation — Brief 1 (superseded by Gupshup opt-in status below; kept until all call sites migrate)
    mobile_verified    = Column(Boolean, default=False, nullable=False)
    mobile_verified_at = Column(DateTime, nullable=True)
    mobile_verified_by = Column(String, ForeignKey("users.id"), nullable=True)
    # Gupshup QR opt-in — replaces mobile_verified as the real consent gate
    whatsapp_opt_in_status = Column(String, nullable=True, default=OptInStatus.PENDING.value)
    opt_in_source          = Column(String, nullable=True)
    opt_in_at              = Column(DateTime, nullable=True)
    matched_phone          = Column(String, nullable=True)
    mismatch_reason        = Column(Text, nullable=True)
    opt_in_actor_id        = Column(String, ForeignKey("users.id"), nullable=True)
    # Employee's own on/off preference — independent of whatsapp_opt_in_status
    # (verification). A verified/opted-in employee can still turn WhatsApp
    # notifications off for themselves; the opt-in record itself is untouched.
    whatsapp_notifications_enabled = Column(Boolean, default=True)
    # Sales module access — JSON array of module tags e.g. '["SALES","INVENTORY"]'
    # ADMIN and MANAGER roles always see all modules regardless of this field.
    module_access_json = Column(Text, nullable=True, default='[]')
    # Per-employee nav tab visibility — JSON array of tab keys e.g. '["TICKETS","SALES"]'
    # NULL means "no restriction set" — falls back to all tenant-enabled tabs.
    # ADMIN and MANAGER roles always see all tenant-enabled tabs regardless of this field.
    tab_access_json = Column(Text, nullable=True)

    tenant = relationship("Tenant", back_populates="users")
    department = relationship("Department", back_populates="users")
    manager = relationship("User", remote_side="User.id", foreign_keys="User.manager_id", backref="reports")
    mobile_verified_by_user = relationship("User", remote_side="User.id", foreign_keys="[User.mobile_verified_by]")
    opt_in_actor = relationship("User", remote_side="User.id", foreign_keys="[User.opt_in_actor_id]")
    created_tickets = relationship("Ticket", foreign_keys="Ticket.created_by_id", back_populates="created_by")
    assigned_tickets = relationship("Ticket", foreign_keys="Ticket.current_assignee_id", back_populates="current_assignee")


class EmployeeDocument(Base):
    """KYC-style documents (identity proof, address proof, etc.) attached to an employee record."""
    __tablename__ = "employee_documents"
    id = Column(String, primary_key=True, default=new_id)
    tenant_id = Column(String, ForeignKey("tenants.id"), nullable=False)
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    doc_type = Column(String, nullable=False)      # IDENTITY_PROOF / ADDRESS_PROOF / OTHER
    label = Column(String, nullable=True)          # optional custom label e.g. "Aadhaar Card"
    file_name = Column(String, nullable=False)
    file_path = Column(String, nullable=False)
    file_type = Column(String, nullable=True)
    file_size = Column(Integer, nullable=True)
    uploaded_by = Column(String, ForeignKey("users.id"), nullable=True)
    is_deleted = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class EmployeeGadget(Base):
    """A client-provided gadget (laptop, SIM, etc.) issued to an employee, tracked for records."""
    __tablename__ = "employee_gadgets"
    id = Column(String, primary_key=True, default=new_id)
    tenant_id = Column(String, ForeignKey("tenants.id"), nullable=False)
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    gadget_name = Column(String, nullable=False)   # e.g. "Laptop", "SIM Card"
    serial_number = Column(String, nullable=True)
    provided_by = Column(String, nullable=True)    # e.g. client / company name
    notes = Column(Text, nullable=True)
    is_deleted = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    created_by = Column(String, ForeignKey("users.id"), nullable=True)


class EmployeeGadgetDocument(Base):
    """Proof/receipt documents attached to a specific EmployeeGadget."""
    __tablename__ = "employee_gadget_documents"
    id = Column(String, primary_key=True, default=new_id)
    gadget_id = Column(String, ForeignKey("employee_gadgets.id"), nullable=False)
    file_name = Column(String, nullable=False)
    file_path = Column(String, nullable=False)
    file_type = Column(String, nullable=True)
    file_size = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


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
    # P1-06: ticket enhancements
    ticket_category   = Column(String, default="NORMAL")   # NORMAL / HELP
    evidence_required = Column(Boolean, default=False)     # replaces proof_required
    is_deleted = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    tenant = relationship("Tenant", back_populates="tickets")
    created_by = relationship("User", foreign_keys=[created_by_id], back_populates="created_tickets")
    current_assignee = relationship("User", foreign_keys=[current_assignee_id], back_populates="assigned_tickets")
    comments = relationship("TicketComment", back_populates="ticket", order_by="TicketComment.created_at")
    events = relationship("TicketEvent", back_populates="ticket", order_by="TicketEvent.created_at")
    helpers = relationship("TicketAssignee", back_populates="ticket", foreign_keys="TicketAssignee.ticket_id")
    media = relationship("MediaUpload", primaryjoin="and_(MediaUpload.entity_type=='ticket', foreign(MediaUpload.entity_id)==Ticket.id)", viewonly=True)
    whatsapp_logs = relationship(
        "WhatsAppMessageLog",
        primaryjoin="and_(WhatsAppMessageLog.related_entity_type=='ticket', foreign(WhatsAppMessageLog.related_entity_id)==Ticket.id)",
        viewonly=True,
        order_by="WhatsAppMessageLog.created_at",
    )

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
    evidence_required = Column(Boolean, default=False)  # P1-08: replaces proof_required on template
    assigned_to_role = Column(String)
    assigned_to_dept_id = Column(String, ForeignKey("departments.id"), nullable=True)
    assigned_to_user_id = Column(String, ForeignKey("users.id"), nullable=True)
    reminder_hours_before = Column(Integer, default=2)
    reminder_repeat_hours = Column(Integer, default=4)
    is_active = Column(Boolean, default=True)
    is_deleted = Column(Boolean, default=False)
    is_recurring = Column(Boolean, default=True)   # auto-schedule next on completion
    # E-14: extended frequency fields (NULL = use legacy `frequency` column)
    frequency_type   = Column(String, nullable=True)
    frequency_config = Column(JSON,   nullable=True)
    # Due date vs due time: ANYTIME = due sometime that day (no time enforced);
    # FIXED_TIME = must be done by due_time ("HH:MM") that day.
    due_time_mode    = Column(String, default="ANYTIME")
    due_time         = Column(String, nullable=True)  # "HH:MM", only used when due_time_mode == FIXED_TIME
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
    # P1-08: checklist assignment enhancements
    delay_reason      = Column(Text)              # mandatory when OVERDUE completion
    evidence_required = Column(Boolean)           # inherited from template at assignment creation
    is_deleted        = Column(Boolean, default=False)  # P6-03: soft delete
    is_flagged        = Column(Boolean, default=False)
    flagged_reason    = Column(String)
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


class PushSubscription(Base):
    """Web Push subscription per device — Phase 6 PWA push channel."""
    __tablename__ = "push_subscriptions"
    id = Column(String, primary_key=True, default=new_id)
    tenant_id = Column(String, ForeignKey("tenants.id"), nullable=False)
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    endpoint = Column(Text, nullable=False)
    p256dh_key = Column(String, nullable=False)
    auth_key = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    last_used_at = Column(DateTime)

    user = relationship("User")

class MediaUpload(Base):
    """Shared media table for tickets, checklists & sales order line items — Phase 0-E-1"""
    __tablename__ = "media_uploads"
    id = Column(String, primary_key=True, default=new_id)
    tenant_id = Column(String, ForeignKey("tenants.id"), nullable=False)
    entity_type = Column(String, nullable=False)   # "ticket" / "checklist" / "sales_order_item"
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
    fms_s         = Column(String)   # Flow Board singular label e.g. "Production Board"
    fms_p         = Column(String)   # Flow Board plural label
    industry      = Column(String)   # which preset was last applied
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


class PerformanceFormula(Base):
    """Tenant-level configurable performance score formula with history.

    Only one row per tenant has is_active=True — the current formula.
    Previous formulas are retained (is_active=False) for audit history.
    weights: {"ticket_on_time": 40, "ticket_completion": 20,
               "checklist_compliance": 25, "checklist_on_time": 15, "fms_on_time": 0}
    """
    __tablename__ = "performance_formulas"
    id           = Column(String,  primary_key=True, default=new_id)
    tenant_id    = Column(String,  ForeignKey("tenants.id"), nullable=False)
    label        = Column(String,  nullable=True)   # optional human name
    weights      = Column(JSON,    nullable=False)
    is_active    = Column(Boolean, default=True)
    created_at   = Column(DateTime, default=datetime.utcnow)
    created_by_id = Column(String, ForeignKey("users.id"), nullable=True)

    tenant     = relationship("Tenant")
    created_by = relationship("User", foreign_keys=[created_by_id])


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
    sub_module_tag               = Column(String,  nullable=True)   # PMS|DISPATCH|INVOICE|CUSTOM
    submodule_id                 = Column(String,  ForeignKey("library_submodule_definitions.id"), nullable=True)
    completion_note_required     = Column(Boolean, default=False)
    evidence_required            = Column(Boolean, default=False)   # P1-07
    custom_fields_json           = Column(Text,    default="[]")    # JSON array of custom field defs
    split_enabled                = Column(Boolean, default=False)   # FMS Auto-Split Engine
    split_target_field           = Column(String,  nullable=True)   # custom field name holding target/expected value
    split_actual_field           = Column(String,  nullable=True)   # custom field name holding entered/received value

    template   = relationship("LibraryFlowTemplate", back_populates="stages")
    submodule  = relationship("LibrarySubmoduleDefinition", foreign_keys=[submodule_id])


class LibrarySubmoduleDefinition(Base):
    """A structured data-entry form definition (sub-module).
    sub_module_type: PMS | DISPATCH | INVOICE | CUSTOM
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
    sub_module_type  = Column(String,  default="CUSTOM")   # PMS|DISPATCH|INVOICE|CUSTOM
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
    fms_s       = Column(String)   # Flow Board singular label
    fms_p       = Column(String)   # Flow Board plural label
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

class FMSFlowGroup(Base):
    """
    Named grouping of 2+ existing FMSFlows, shown in place of its members in
    the top-of-FMS-page flow dropdown (FMS Flow Grouping & Duplication brief).
    A flow can belong to at most one group (see FMSFlow.group_id) — no join
    table needed.
    """
    __tablename__ = "fms_flow_groups"
    id         = Column(String,  primary_key=True, default=new_id)
    tenant_id  = Column(String,  ForeignKey("tenants.id"), nullable=False)
    name       = Column(String,  nullable=False)
    is_active  = Column(Boolean, default=True)
    is_deleted = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    tenant = relationship("Tenant")
    flows  = relationship("FMSFlow", back_populates="group")


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
    restrict_to_assignee     = Column(Boolean, default=False)         # only the current-stage assignee (or that stage's configured default_assignee) may act on a ticket
    library_flow_id          = Column(String)                        # source library template (if any)
    library_version_at_deploy= Column(Integer)                       # version at time of deploy (2-B-5)
    ticket_form_fields_json  = Column(Text,    default="[]")          # JSON array of custom field defs for ticket creation form
    closing_rule_json        = Column(Text)                           # {col_id, op, value} — must hold true before a ticket on this flow can close
    group_id                 = Column(String,  ForeignKey("fms_flow_groups.id"), nullable=True)  # null = ungrouped (R1)
    created_by_id            = Column(String,  ForeignKey("users.id"))
    created_at               = Column(DateTime, default=datetime.utcnow)
    updated_at               = Column(DateTime, default=datetime.utcnow)

    tenant     = relationship("Tenant")
    created_by = relationship("User", foreign_keys=[created_by_id])
    group      = relationship("FMSFlowGroup", back_populates="flows")
    stages     = relationship("FMSStage",  back_populates="flow",
                              order_by="FMSStage.order",
                              cascade="all, delete-orphan")
    tickets    = relationship("FMSTicket", back_populates="flow")


class FMSStage(Base):
    """
    One stage inside an FMSFlow.
    Fields per §10.2: name, order, target_tat_hours, default_assignee,
    sub_module_tag, is_mandatory, completion_note_required, is_terminal.
    sub_module_tag values: None | PMS | DISPATCH | INVOICE | CUSTOM
    """
    __tablename__ = "fms_stages"
    id                      = Column(String,  primary_key=True, default=new_id)
    flow_id                 = Column(String,  ForeignKey("fms_flows.id"), nullable=False)
    tenant_id               = Column(String,  ForeignKey("tenants.id"), nullable=False)
    name                    = Column(String,  nullable=False)
    description             = Column(Text,    nullable=True)
    order                   = Column(Integer, default=0)
    color                   = Column(String,  default="#3b82f6")
    target_tat_hours        = Column(Float,   nullable=True)         # None = no TaT target; always stored in hours
    target_tat_unit         = Column(String,  default="hours")       # minutes|hours|days — display unit only
    default_assignee_id     = Column(String,  ForeignKey("users.id"), nullable=True)
    sub_module_tag          = Column(String,  nullable=True)         # PMS|DISPATCH|INVOICE|CUSTOM
    deployed_submodule_id   = Column(String,  ForeignKey("library_submodule_definitions.id"), nullable=True)
    is_mandatory            = Column(Boolean, default=True)
    completion_note_required= Column(Boolean, default=False)
    is_terminal             = Column(Boolean, default=False)         # reaching this = COMPLETED
    evidence_required       = Column(Boolean, default=False)         # P1-07: stage-level evidence
    custom_fields_json      = Column(Text,    default="[]")          # JSON array of custom field defs
    is_deleted              = Column(Boolean, default=False)
    split_enabled           = Column(Boolean, default=False)         # FMS Auto-Split Engine (opt-in per stage)
    split_target_field      = Column(String,  nullable=True)         # custom field name holding target/expected value
    split_actual_field      = Column(String,  nullable=True)         # custom field name holding entered/received value

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
    has_qty_discrepancy = Column(Boolean, default=False)  # Phase 0: system-detected — active splits' qty no longer sums to target_qty
    completed_at        = Column(DateTime)
    closed_at           = Column(DateTime)
    is_deleted          = Column(Boolean, default=False)
    stage_assignees_json  = Column(Text, nullable=True)  # {"stage_id": "user_id", ...}
    stage_schedule_json   = Column(Text, nullable=True)  # {"stage_id": {"planned_start": ISO, "planned_end": ISO}}
    ticket_custom_fields_json = Column(Text, nullable=True)  # {"field_id": value, ...} from flow's ticket_form_fields_json
    created_by_id        = Column(String,  ForeignKey("users.id"), nullable=False)
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
    splits           = relationship("FMSTicketSplit", foreign_keys="FMSTicketSplit.ticket_id",
                                    order_by="FMSTicketSplit.created_at",
                                    back_populates="ticket",
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
    split_id         = Column(String,  ForeignKey("fms_ticket_splits.id"), nullable=True)  # Phase 0: which split this history row belongs to
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
    planned_start         = Column(DateTime, nullable=True)  # from stage_schedule_json at entry
    planned_end           = Column(DateTime, nullable=True)  # from stage_schedule_json at entry
    evidence_url          = Column(String,  nullable=True)   # uploaded file path/URL
    evidence_filename     = Column(String,  nullable=True)   # original filename for display
    custom_fields_data_json = Column(Text, nullable=True)    # JSON dict {field_label: value}

    ticket   = relationship("FMSTicket", back_populates="stage_history")
    split    = relationship("FMSTicketSplit", foreign_keys=[split_id])
    stage    = relationship("FMSStage",  foreign_keys=[stage_id])
    assignee = relationship("User",      foreign_keys=[assignee_id])


class FMSTicketSplit(Base):
    """
    Phase 0: a portion of an FMSTicket's quantity progressing independently
    through stages. Every ticket gets exactly one split at creation — splits
    are always the unit of stage-progress, even for tickets nobody has ever
    manually split (see brief §3). The UI hides the concept unless a ticket
    has more than one active split.

    status vocabulary matches FMSTicket.status. is_deleted=True marks a
    split that was fully carved into a child split (qty hit 0) — its story
    ends there, so it's excluded from "active splits" queries.
    """
    __tablename__ = "fms_ticket_splits"
    id                  = Column(String,  primary_key=True, default=new_id)
    tenant_id           = Column(String,  ForeignKey("tenants.id"), nullable=False)
    ticket_id           = Column(String,  ForeignKey("fms_tickets.id"), nullable=False)
    parent_split_id     = Column(String,  ForeignKey("fms_ticket_splits.id"), nullable=True)
    split_label         = Column(String,  nullable=False)  # e.g. "S1", "S2" — auto-numbered per ticket
    qty                 = Column(Integer, nullable=True)
    current_stage_id    = Column(String,  ForeignKey("fms_stages.id"), nullable=True)
    current_assignee_id = Column(String,  ForeignKey("users.id"), nullable=True)
    status              = Column(String,  default="ACTIVE")
    is_deleted          = Column(Boolean, default=False)
    created_at          = Column(DateTime, default=datetime.utcnow)
    updated_at          = Column(DateTime, default=datetime.utcnow)

    # FMS Auto-Split Engine additions (additive, nullable — do not break existing manual-split flow)
    root_ticket_id        = Column(String,  ForeignKey("fms_tickets.id"), nullable=True)  # = ticket_id for 1st-level splits
    split_display_id      = Column(String,  nullable=True)   # hierarchical human id e.g. F-0042-1, F-0042-1-1
    split_sequence         = Column(Integer, nullable=True)   # order among siblings
    split_stage_id         = Column(String,  ForeignKey("fms_stages.id"), nullable=True)  # stage where auto-generated
    target_value_at_split  = Column(Float,   nullable=True)   # snapshot of target at time of split
    entered_value           = Column(Float,   nullable=True)   # value entered that produced this split
    is_remainder            = Column(Boolean, default=False)   # True = shortfall portion staying at current stage
    is_auto_split           = Column(Boolean, default=False)   # True = created by auto-split engine (vs manual)
    last_cumulative_entered = Column(Float,   nullable=True)   # last cumulative total-delivered-so-far submitted for this remainder's actual field; next visit's split amount = new_cumulative - this

    tenant           = relationship("Tenant")
    ticket           = relationship("FMSTicket", foreign_keys=[ticket_id], back_populates="splits")
    parent_split     = relationship("FMSTicketSplit", remote_side=[id])
    current_stage    = relationship("FMSStage", foreign_keys=[current_stage_id])
    current_assignee = relationship("User",     foreign_keys=[current_assignee_id])
    split_stage      = relationship("FMSStage", foreign_keys=[split_stage_id])


class FMSSplitEvidence(Base):
    """
    FMS Auto-Split Engine: optional evidence (photo/pdf/audio/video) attached
    to a specific split entry. Never mandatory (R7). Stored on local disk via
    app/uploads.py::save_upload — traceable to the split, not the parent ticket.
    """
    __tablename__ = "fms_split_evidence"
    id          = Column(String,  primary_key=True, default=new_id)
    tenant_id   = Column(String,  ForeignKey("tenants.id"), nullable=False)
    split_id    = Column(String,  ForeignKey("fms_ticket_splits.id"), nullable=False)
    file_type   = Column(String,  nullable=False)   # photo|pdf|audio|video
    file_url    = Column(String,  nullable=False)
    file_name   = Column(String,  nullable=True)
    uploaded_by = Column(String,  ForeignKey("users.id"), nullable=True)
    created_at  = Column(DateTime, default=datetime.utcnow, nullable=False)

    tenant = relationship("Tenant")
    split  = relationship("FMSTicketSplit", foreign_keys=[split_id])
    uploader = relationship("User", foreign_keys=[uploaded_by])


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


class FMSFieldEditLog(Base):
    """Audit trail for manual edits to a ticket/stage custom-column value made
    directly from the Table view (as opposed to values captured through the
    normal stage-transition flow). Every edit requires a reason; cascaded
    formula recalculations triggered by an edit get their own row here too,
    so the full "what changed and why" chain is reconstructable."""
    __tablename__ = "fms_field_edit_log"
    id           = Column(String,  primary_key=True, default=new_id)
    tenant_id    = Column(String,  ForeignKey("tenants.id"), nullable=False)
    ticket_id    = Column(String,  ForeignKey("fms_tickets.id"), nullable=False)
    stage_id     = Column(String,  ForeignKey("fms_stages.id"), nullable=True)  # null = ticket-level field
    field_id     = Column(String,  nullable=False)
    field_label  = Column(String,  nullable=True)
    old_value    = Column(String,  nullable=True)
    new_value    = Column(String,  nullable=True)
    reason       = Column(Text,    nullable=False)
    is_cascade   = Column(Boolean, default=False)  # auto-recalculated formula, not the direct edit
    edited_by_id = Column(String,  ForeignKey("users.id"), nullable=True)
    edited_at    = Column(DateTime, default=datetime.utcnow)

    ticket    = relationship("FMSTicket", foreign_keys=[ticket_id])
    stage     = relationship("FMSStage",  foreign_keys=[stage_id])
    edited_by = relationship("User",      foreign_keys=[edited_by_id])


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


# ═══════════════════════════════════════════════════════════════════
# Enhancement Pass — Setup Entity Tables (Phase 1, Section 3.1)
# ═══════════════════════════════════════════════════════════════════

class Vendor(Base):
    __tablename__ = "vendors"
    id             = Column(String,  primary_key=True, default=new_id)
    tenant_id      = Column(String,  ForeignKey("tenants.id"), nullable=False)
    name           = Column(String,  nullable=False)
    contact_person = Column(String)
    phone          = Column(String)
    email          = Column(String)
    address        = Column(Text)
    parts_supplied = Column(Text)
    notes          = Column(Text)
    is_active      = Column(Boolean, default=True)
    is_deleted     = Column(Boolean, default=False)
    approval_status= Column(String, default="APPROVED")  # APPROVED | PENDING — pending entries were auto-created from a ticket's "new entry" field
    created_by_id  = Column(String,  ForeignKey("users.id"))
    created_at     = Column(DateTime, default=datetime.utcnow)
    updated_at     = Column(DateTime, default=datetime.utcnow)

    tenant     = relationship("Tenant")
    created_by = relationship("User", foreign_keys=[created_by_id])


class RawMaterial(Base):
    __tablename__ = "raw_materials"
    id             = Column(String,  primary_key=True, default=new_id)
    tenant_id      = Column(String,  ForeignKey("tenants.id"), nullable=False)
    name           = Column(String,  nullable=False)
    unit           = Column(String)
    description    = Column(Text)
    major_supplier = Column(String)
    notes          = Column(Text)
    is_active     = Column(Boolean, default=True)
    is_deleted    = Column(Boolean, default=False)
    approval_status = Column(String, default="APPROVED")  # APPROVED | PENDING
    created_by_id = Column(String,  ForeignKey("users.id"))
    created_at    = Column(DateTime, default=datetime.utcnow)
    updated_at    = Column(DateTime, default=datetime.utcnow)

    tenant     = relationship("Tenant")
    created_by = relationship("User", foreign_keys=[created_by_id])


class Customer(Base):
    __tablename__ = "customers"
    id             = Column(String,  primary_key=True, default=new_id)
    tenant_id      = Column(String,  ForeignKey("tenants.id"), nullable=False)
    name           = Column(String,  nullable=False)
    contact_person = Column(String)
    phone          = Column(String)
    email          = Column(String)
    address        = Column(Text)
    notes          = Column(Text)
    is_active      = Column(Boolean, default=True)
    is_deleted     = Column(Boolean, default=False)
    approval_status= Column(String, default="APPROVED")  # APPROVED | PENDING
    created_by_id  = Column(String,  ForeignKey("users.id"))
    created_at     = Column(DateTime, default=datetime.utcnow)
    updated_at     = Column(DateTime, default=datetime.utcnow)

    assigned_agent_id = Column(String, ForeignKey("users.id"), nullable=True)
    customer_tier     = Column(String, default="UNRANKED")
    last_contacted_at = Column(DateTime, nullable=True)
    contact_freq_days = Column(Integer, default=30)
    price_list_id     = Column(String, ForeignKey("price_lists.id"), nullable=True)
    gstin             = Column(String, nullable=True)
    credit_limit      = Column(Float,  nullable=True)
    billing_address   = Column(Text,   nullable=True)
    shipping_address  = Column(Text,   nullable=True)
    default_payment_terms = Column(String, nullable=True)

    tenant         = relationship("Tenant")
    created_by     = relationship("User", foreign_keys=[created_by_id])
    assigned_agent = relationship("User", foreign_keys=[assigned_agent_id])
    price_list     = relationship("PriceList", foreign_keys=[price_list_id])


class CRMCallLog(Base):
    """
    Immutable log of every contact attempt with a customer.
    Only follow_up_done is updated after creation.
    outcome: CONNECTED / NO_ANSWER / CALLBACK / ORDER_PLACED / NOT_INTERESTED
    """
    __tablename__ = "crm_call_logs"
    id             = Column(String,  primary_key=True, default=new_id)
    tenant_id      = Column(String,  ForeignKey("tenants.id"),   nullable=False)
    customer_id    = Column(String,  ForeignKey("customers.id"), nullable=False)
    agent_id       = Column(String,  ForeignKey("users.id"),     nullable=False)
    contacted_at   = Column(DateTime, default=datetime.utcnow)
    outcome        = Column(String,  nullable=False)
    follow_up_at   = Column(DateTime, nullable=True)
    follow_up_done = Column(Boolean, default=False)
    order_id       = Column(String,  nullable=True)  # FK to sales_orders added in Brief 05
    notes          = Column(Text,    nullable=True)
    created_at     = Column(DateTime, default=datetime.utcnow)

    tenant   = relationship("Tenant")
    customer = relationship("Customer", foreign_keys=[customer_id])
    agent    = relationship("User",     foreign_keys=[agent_id])


class EndProduct(Base):
    """
    Setup > End Products — mirrors Sales Catalog ProductVariant rows (matched
    by sku_code, see app/sales_catalog_sync.py), used by FMS/Delegations
    linked entities. category_id/sub_category_id mirror the linked variant's
    Product.sub_category hierarchy so both lists share the same taxonomy.
    """
    __tablename__ = "end_products"
    id               = Column(String,  primary_key=True, default=new_id)
    tenant_id        = Column(String,  ForeignKey("tenants.id"), nullable=False)
    name             = Column(String,  nullable=False)
    sku_code         = Column(String)
    unit             = Column(String)
    description      = Column(Text)
    category_id      = Column(String,  ForeignKey("categories.id"), nullable=True)
    sub_category_id  = Column(String,  ForeignKey("sub_categories.id"), nullable=True)
    is_active        = Column(Boolean, default=True)
    is_deleted       = Column(Boolean, default=False)
    approval_status  = Column(String, default="APPROVED")  # APPROVED | PENDING
    created_by_id    = Column(String,  ForeignKey("users.id"))
    created_at       = Column(DateTime, default=datetime.utcnow)
    updated_at       = Column(DateTime, default=datetime.utcnow)

    tenant       = relationship("Tenant")
    created_by   = relationship("User", foreign_keys=[created_by_id])
    category     = relationship("Category", foreign_keys=[category_id])
    sub_category = relationship("SubCategory", foreign_keys=[sub_category_id])


class CustomReferenceList(Base):
    __tablename__ = "custom_reference_lists"
    id            = Column(String,  primary_key=True, default=new_id)
    tenant_id     = Column(String,  ForeignKey("tenants.id"), nullable=False)
    list_name     = Column(String,  nullable=False)
    is_active     = Column(Boolean, default=True)
    is_deleted    = Column(Boolean, default=False)
    created_by_id = Column(String,  ForeignKey("users.id"))
    created_at    = Column(DateTime, default=datetime.utcnow)

    tenant     = relationship("Tenant")
    created_by = relationship("User", foreign_keys=[created_by_id])
    items      = relationship("CustomReferenceItem", back_populates="ref_list",
                              order_by="CustomReferenceItem.sort_order",
                              cascade="all, delete-orphan")


class CustomReferenceItem(Base):
    __tablename__ = "custom_reference_items"
    id         = Column(String,  primary_key=True, default=new_id)
    list_id    = Column(String,  ForeignKey("custom_reference_lists.id"), nullable=False)
    tenant_id  = Column(String,  ForeignKey("tenants.id"), nullable=False)
    value      = Column(String,  nullable=False)
    sort_order = Column(Integer, default=0)
    is_active  = Column(Boolean, default=True)
    is_deleted = Column(Boolean, default=False)
    approval_status = Column(String, default="APPROVED")  # APPROVED | PENDING
    created_at = Column(DateTime, default=datetime.utcnow)

    ref_list = relationship("CustomReferenceList", back_populates="items")
    tenant   = relationship("Tenant")


class LinkedEntityReference(Base):
    """Polymorphic table linking any entity to a ticket/checklist/FMS ticket.
    No FK on parent_id or entity_id — resolved at query time for flexibility."""
    __tablename__ = "linked_entity_references"
    id           = Column(String, primary_key=True, default=new_id)
    tenant_id    = Column(String, ForeignKey("tenants.id"), nullable=False)
    parent_type  = Column(String, nullable=False)  # TICKET / CHECKLIST_ASSIGNMENT / FMS_TICKET
    parent_id    = Column(String, nullable=False)
    entity_type  = Column(String, nullable=False)  # CUSTOMER / END_PRODUCT / MATERIAL / VENDOR / CUSTOM_LIST / OTHER
    entity_id    = Column(String)
    entity_label = Column(String)  # snapshot of entity name at time of linking
    custom_text  = Column(String)  # used when entity_type is OTHER
    created_by_id= Column(String, ForeignKey("users.id"))
    created_at   = Column(DateTime, default=datetime.utcnow)

    tenant     = relationship("Tenant")
    created_by = relationship("User", foreign_keys=[created_by_id])


class LoginEvent(Base):
    __tablename__ = "login_events"
    id         = Column(String, primary_key=True, default=new_id)
    tenant_id  = Column(String, ForeignKey("tenants.id"), nullable=False)
    user_id    = Column(String, ForeignKey("users.id"), nullable=False)
    logged_in_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    tenant = relationship("Tenant")
    user   = relationship("User")


class WhatsAppMessageLog(Base):
    """
    Foundation table — every outbound WhatsApp send attempt across all pipelines
    is logged here. Built once; reused by every template brief going forward.
    Extended for the Gupshup migration with per-send template metadata and a
    full delivery-status/webhook-payload history (Section 4.4 of the brief).
    """
    __tablename__ = "whatsapp_message_log"
    id                   = Column(String, primary_key=True, default=new_id)
    tenant_id            = Column(String, ForeignKey("tenants.id"), nullable=False)
    template_name        = Column(String, nullable=False)
    recipient_user_id    = Column(String, ForeignKey("users.id"), nullable=True)
    recipient_phone      = Column(String, nullable=False)
    variables_json       = Column(Text, nullable=False)   # JSON list, approved order
    status               = Column(String, nullable=False)  # SENT / FAILED / SKIPPED_UNVERIFIED
    error_message        = Column(Text, nullable=True)
    related_entity_type  = Column(String, nullable=True)   # 'ticket' for this brief
    related_entity_id    = Column(String, nullable=True)
    attempt_count        = Column(Integer, default=1)
    created_at           = Column(DateTime, default=datetime.utcnow)
    last_attempted_at    = Column(DateTime, default=datetime.utcnow)
    # Gupshup migration — Section 4.4
    template_id                 = Column(String, nullable=True)   # Gupshup/Facebook template identifier used for this send
    template_category           = Column(String, nullable=True)   # UTILITY / MARKETING / AUTHENTICATION
    delivery_status_history      = Column(JSON, nullable=False, default=list)  # [{status, timestamp}, ...]
    raw_status_webhook_payloads  = Column(JSON, nullable=False, default=list)  # verbatim payload per status webhook

    tenant    = relationship("Tenant")
    recipient = relationship("User", foreign_keys=[recipient_user_id])


class WhatsAppConsentEvent(Base):
    """
    Append-only evidentiary log of every consent-related event (opt-in, mismatch,
    correction, manual override, opt-out) — Section 4.3 of the Gupshup brief.
    Rows are never updated or deleted after creation; this is the record Sahil
    would submit in a Meta/Gupshup appeal if a tenant's number is flagged.
    """
    __tablename__ = "whatsapp_consent_events"
    id                   = Column(String, primary_key=True, default=new_id)
    tenant_id            = Column(String, ForeignKey("tenants.id"), nullable=False)
    employee_id          = Column(String, ForeignKey("users.id"), nullable=True)   # null = unmatched inbound
    event_type           = Column(String, nullable=False)   # ConsentEventType
    phone_number         = Column(String, nullable=False)   # E.164
    gupshup_message_id   = Column(String, nullable=True)
    raw_webhook_payload  = Column(JSON, nullable=True)
    source               = Column(String, nullable=True)    # OptInSource
    actor_id             = Column(String, ForeignKey("users.id"), nullable=True)  # admin who performed a manual action
    notes                = Column(Text, nullable=True)
    created_at           = Column(DateTime, default=datetime.utcnow, nullable=False)

    tenant   = relationship("Tenant")
    employee = relationship("User", foreign_keys=[employee_id])
    actor    = relationship("User", foreign_keys=[actor_id])


class TrainingMaterialCategory(Base):
    __tablename__ = "training_material_categories"

    id         = Column(String,  primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id  = Column(String,  nullable=False)
    name       = Column(String,  nullable=False)
    is_active  = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class TrainingMaterial(Base):
    __tablename__ = "training_materials"

    id             = Column(String,  primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id      = Column(String,  nullable=False)
    title          = Column(String,  nullable=False)
    description    = Column(Text,    nullable=True)
    file_name      = Column(String,  nullable=False)
    file_path      = Column(String,  nullable=False)
    file_type      = Column(String,  nullable=True)
    file_size      = Column(Integer, nullable=True)
    category       = Column(String,  nullable=True)
    department_id  = Column(String,  nullable=True)
    tags           = Column(String,  nullable=True)
    uploaded_by_id = Column(String,  nullable=False)
    is_deleted     = Column(Boolean, default=False)
    created_at     = Column(DateTime, default=datetime.utcnow)


class KnowledgeItem(Base):
    """Knowledge Repository — documents, videos, audios, and links uploaded per tenant."""
    __tablename__ = "knowledge_items"
    id            = Column(String,  primary_key=True, default=new_id)
    tenant_id     = Column(String,  ForeignKey("tenants.id"), nullable=False)
    title         = Column(String,  nullable=False)
    description   = Column(Text,    nullable=True)
    category      = Column(String,  nullable=True)   # free-text category / folder
    tags          = Column(String,  nullable=True)   # comma-separated
    media_kind    = Column(String,  nullable=True)   # document | video | audio | image | link
    file_url      = Column(String,  nullable=True)   # served path for uploaded files
    file_name     = Column(String,  nullable=True)   # original filename
    file_type     = Column(String,  nullable=True)   # MIME type
    file_size     = Column(Integer, nullable=True)   # bytes
    external_url  = Column(String,  nullable=True)   # for link-type items
    department_id = Column(String,  nullable=True)   # optional department scoping (migrated from TrainingMaterial)
    created_by_id = Column(String,  ForeignKey("users.id"), nullable=True)
    created_at    = Column(DateTime, default=datetime.utcnow)
    updated_at    = Column(DateTime, default=datetime.utcnow)
    is_deleted    = Column(Boolean,  default=False)

    tenant      = relationship("Tenant")
    created_by  = relationship("User", foreign_keys=[created_by_id])


class TicketKnowledgeLink(Base):
    """Phase 3: links a ticket (typically a closed delegation) to a KnowledgeItem
    for future reference."""
    __tablename__ = "ticket_knowledge_links"
    id                 = Column(String, primary_key=True, default=new_id)
    tenant_id          = Column(String, ForeignKey("tenants.id"), nullable=False)
    ticket_id          = Column(String, ForeignKey("tickets.id"), nullable=False)
    knowledge_item_id  = Column(String, ForeignKey("knowledge_items.id"), nullable=False)
    linked_by_id       = Column(String, ForeignKey("users.id"), nullable=False)
    created_at         = Column(DateTime, default=datetime.utcnow)

    tenant         = relationship("Tenant")
    ticket         = relationship("Ticket", backref="knowledge_links")
    knowledge_item = relationship("KnowledgeItem")
    linked_by      = relationship("User", foreign_keys=[linked_by_id])


class ChecklistKnowledgeLink(Base):
    """Links a checklist template to a KnowledgeItem for quick reference at
    creation and at completion time."""
    __tablename__ = "checklist_knowledge_links"
    id                 = Column(String, primary_key=True, default=new_id)
    tenant_id          = Column(String, ForeignKey("tenants.id"), nullable=False)
    template_id        = Column(String, ForeignKey("checklist_templates.id"), nullable=False)
    knowledge_item_id  = Column(String, ForeignKey("knowledge_items.id"), nullable=False)
    linked_by_id       = Column(String, ForeignKey("users.id"), nullable=False)
    created_at         = Column(DateTime, default=datetime.utcnow)

    tenant         = relationship("Tenant")
    template       = relationship("ChecklistTemplate", backref="knowledge_links")
    knowledge_item = relationship("KnowledgeItem")
    linked_by      = relationship("User", foreign_keys=[linked_by_id])


class FMSTicketKnowledgeLink(Base):
    """Links an FMS ticket to a KnowledgeItem for quick reference at creation
    and at close time."""
    __tablename__ = "fms_ticket_knowledge_links"
    id                 = Column(String, primary_key=True, default=new_id)
    tenant_id          = Column(String, ForeignKey("tenants.id"), nullable=False)
    ticket_id          = Column(String, ForeignKey("fms_tickets.id"), nullable=False)
    knowledge_item_id  = Column(String, ForeignKey("knowledge_items.id"), nullable=False)
    linked_by_id       = Column(String, ForeignKey("users.id"), nullable=False)
    created_at         = Column(DateTime, default=datetime.utcnow)

    tenant         = relationship("Tenant")
    ticket         = relationship("FMSTicket", backref="knowledge_links")
    knowledge_item = relationship("KnowledgeItem")
    linked_by      = relationship("User", foreign_keys=[linked_by_id])


_DEFAULT_UOMS = [
    {"name": "Piece",    "abbreviation": "pcs"},
    {"name": "Kilogram", "abbreviation": "kg"},
    {"name": "Gram",     "abbreviation": "g"},
    {"name": "Litre",    "abbreviation": "L"},
    {"name": "Metre",    "abbreviation": "m"},
    {"name": "Box",      "abbreviation": "box"},
    {"name": "Dozen",    "abbreviation": "doz"},
]


class UnitOfMeasure(Base):
    __tablename__ = "units_of_measure"
    id           = Column(String,  primary_key=True, default=new_id)
    tenant_id    = Column(String,  ForeignKey("tenants.id"), nullable=False)
    name         = Column(String,  nullable=False)
    abbreviation = Column(String,  nullable=False)
    is_active    = Column(Boolean, default=True)
    is_deleted   = Column(Boolean, default=False)
    created_at   = Column(DateTime, default=datetime.utcnow)

    tenant = relationship("Tenant")


class ProductSchemaField(Base):
    """
    Defines the custom attribute fields for this tenant's product catalog.
    Tenants configure these in Setup. Products store actual values in attributes_json.
    field_type options: text | number | dropdown | boolean
    options_json: ["Option A", "Option B"] for dropdown type, else "[]"
    """
    __tablename__ = "product_schema_fields"
    id           = Column(String,  primary_key=True, default=new_id)
    tenant_id    = Column(String,  ForeignKey("tenants.id"), nullable=False)
    label        = Column(String,  nullable=False)
    field_type   = Column(String,  default="text")
    options_json = Column(Text,    default="[]")
    sort_order   = Column(Integer, default=0)
    is_required  = Column(Boolean, default=False)
    is_active    = Column(Boolean, default=True)
    created_at   = Column(DateTime, default=datetime.utcnow)

    tenant = relationship("Tenant")


class Category(Base):
    """Tenant-scoped top-level catalog category (Catalog Hierarchy Phase 1)."""
    __tablename__ = "categories"
    id         = Column(String,  primary_key=True, default=new_id)
    tenant_id  = Column(String,  ForeignKey("tenants.id"), nullable=False)
    name       = Column(String,  nullable=False)
    is_active  = Column(Boolean, default=True)
    is_deleted = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    tenant = relationship("Tenant")


class SubCategory(Base):
    """Belongs to exactly one Category (Catalog Hierarchy Phase 1)."""
    __tablename__ = "sub_categories"
    id          = Column(String,  primary_key=True, default=new_id)
    tenant_id   = Column(String,  ForeignKey("tenants.id"),  nullable=False)
    category_id = Column(String,  ForeignKey("categories.id"), nullable=False)
    name        = Column(String,  nullable=False)
    is_active   = Column(Boolean, default=True)
    is_deleted  = Column(Boolean, default=False)
    created_at  = Column(DateTime, default=datetime.utcnow)

    tenant   = relationship("Tenant")
    category = relationship("Category")


class Product(Base):
    """
    Parent catalog entry per tenant — shared attributes only. Not directly
    sellable/stockable/priceable; see ProductVariant for the sellable SKU.

    attributes_json: dict of {label: value} matching ProductSchemaField labels,
        shared across all variants of this product.
        e.g. {"GSM": "180", "Width": "44 inch", "Composition": "100% Cotton"}

    base_unit_id: default unit for variants; a variant may override it.
    """
    __tablename__ = "products"
    id                  = Column(String,  primary_key=True, default=new_id)
    tenant_id           = Column(String,  ForeignKey("tenants.id"), nullable=False)
    name                = Column(String,  nullable=False)
    description         = Column(Text,    nullable=True)
    sub_category_id     = Column(String,  ForeignKey("sub_categories.id"), nullable=True)
    base_unit_id        = Column(String,  ForeignKey("units_of_measure.id"), nullable=True)
    attributes_json     = Column(Text,    default="{}")
    is_active           = Column(Boolean, default=True)
    is_deleted          = Column(Boolean, default=False)
    created_by_id       = Column(String,  ForeignKey("users.id"), nullable=True)
    created_at          = Column(DateTime, default=datetime.utcnow)
    updated_at          = Column(DateTime, default=datetime.utcnow)

    tenant       = relationship("Tenant")
    sub_category = relationship("SubCategory")
    base_unit    = relationship("UnitOfMeasure", foreign_keys=[base_unit_id])
    created_by   = relationship("User", foreign_keys=[created_by_id])
    variants     = relationship("ProductVariant", back_populates="product",
                                order_by="ProductVariant.created_at")


class ProductVariant(Base):
    """
    The sellable/stockable/priceable unit — a SKU. Belongs to one Product
    (the shared-attribute parent). Every downstream Sales table (stock,
    pricing, orders, tiering) keys off variant_id, not product_id.

    variant_label: display convenience, e.g. "Red / King" — auto-built from
        variant_attributes_json when not explicitly set.
    base_unit_id: nullable — inherits Product.base_unit_id when null.
    media_urls_json: ordered list of file paths relative to /static/, e.g.
        ["uploads/{tenant_id}/products/{variant_id}/img1.jpg"]. Max 8 images.
    product_tier: set by AI job (Brief 07). Default UNRANKED.
    low_stock_threshold: godown dashboard uses this to flag low stock.
    end_product_id: mirrors this variant into Setup > End Products (used by
        FMS/Delegations linked entities) so both lists stay in sync.
    """
    __tablename__ = "product_variants"
    id                      = Column(String,  primary_key=True, default=new_id)
    tenant_id               = Column(String,  ForeignKey("tenants.id"), nullable=False)
    product_id              = Column(String,  ForeignKey("products.id"), nullable=False)
    sku_code                = Column(String,  nullable=False)
    variant_label           = Column(String,  nullable=True)
    variant_attributes_json = Column(Text,    default="{}")
    base_unit_id            = Column(String,  ForeignKey("units_of_measure.id"), nullable=True)
    media_urls_json         = Column(Text,    default="[]")
    product_tier            = Column(String,  default="UNRANKED")
    low_stock_threshold     = Column(Float,   nullable=True)
    end_product_id          = Column(String,  ForeignKey("end_products.id"), nullable=True)
    is_active               = Column(Boolean, default=True)
    is_deleted              = Column(Boolean, default=False)
    created_by_id           = Column(String,  ForeignKey("users.id"), nullable=True)
    created_at              = Column(DateTime, default=datetime.utcnow)
    updated_at              = Column(DateTime, default=datetime.utcnow)

    tenant      = relationship("Tenant")
    product     = relationship("Product", back_populates="variants")
    base_unit   = relationship("UnitOfMeasure", foreign_keys=[base_unit_id])
    end_product = relationship("EndProduct", foreign_keys=[end_product_id])
    created_by  = relationship("User", foreign_keys=[created_by_id])


class ProductStock(Base):
    """
    Live stock snapshot per variant, optionally split by branch.

    branch_id IS NULL: the tenant-wide aggregate row — always exactly
        one per variant (enforced in code, not just the composite unique
        index below). This is the ONLY row order reservation/fulfillment,
        PO receiving, and in-transit tracking ever touch — those flows are
        intentionally branch-agnostic.
    branch_id = X: a per-branch breakdown row, written alongside
        (never instead of) the aggregate row whenever stock-in/adjustment
        specifies a branch — see handle_stock_in/handle_stock_adjustment.

    qty_available  = physical stock minus active reservations
    qty_reserved   = sum of ACTIVE stock_reservations (added in Brief 04;
        only meaningful on the aggregate row — reservations are never
        branch-scoped)
    qty_in_transit = sum of open PO items not yet received (aggregate row only)
    avg_cost       = weighted average buy cost (updated on every STOCK_IN)
    """
    __tablename__ = "product_stock"
    id              = Column(String,  primary_key=True, default=new_id)
    variant_id      = Column(String,  ForeignKey("product_variants.id"), nullable=False)
    tenant_id       = Column(String,  ForeignKey("tenants.id"),  nullable=False)
    branch_id       = Column(String,  ForeignKey("branches.id"), nullable=True)
    qty_available   = Column(Float,   default=0.0)
    qty_reserved    = Column(Float,   default=0.0)   # managed in Brief 04
    qty_in_transit  = Column(Float,   default=0.0)
    avg_cost        = Column(Float,   nullable=True)
    last_updated_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("variant_id", "branch_id", name="uq_product_stock_variant_branch"),
    )

    variant = relationship("ProductVariant")
    tenant  = relationship("Tenant")
    branch  = relationship("Branch")


class StockLedgerEntry(Base):
    """
    Immutable append-only log of every stock movement.
    Never update or delete rows in this table.

    movement_type values:
      STOCK_IN    — godown receives goods (from supplier)
      STOCK_OUT   — order dispatched (added in Brief 04)
      ADJUSTMENT  — manual correction by godown/admin
      PO_RECEIPT  — purchase order partially/fully received
      RESERVATION — qty held for order (added in Brief 04)
      RELEASE     — reservation cancelled (added in Brief 04)
      RETURN      — goods returned from customer
    """
    __tablename__ = "stock_ledger"
    id              = Column(String,  primary_key=True, default=new_id)
    tenant_id       = Column(String,  ForeignKey("tenants.id"),  nullable=False)
    variant_id      = Column(String,  ForeignKey("product_variants.id"), nullable=False)
    movement_type   = Column(String,  nullable=False)
    qty             = Column(Float,   nullable=False)
    unit_cost       = Column(Float,   nullable=True)
    reference_type  = Column(String,  nullable=True)   # "PO" | "ORDER" | "MANUAL"
    reference_id    = Column(String,  nullable=True)   # PO id or Order id
    notes           = Column(Text,    nullable=True)
    actor_id        = Column(String,  ForeignKey("users.id"), nullable=True)
    created_at      = Column(DateTime, default=datetime.utcnow)

    tenant  = relationship("Tenant")
    variant = relationship("ProductVariant")
    actor   = relationship("User", foreign_keys=[actor_id])


class StockLot(Base):
    """
    FIFO cost lot (2026-07) — one row per physical stock-receipt event
    (a PO receipt, or a manual stock-in with a known unit cost). Unlike
    ProductStock.avg_cost (a single blended moving average), each lot keeps
    its own unit_cost and qty_remaining so order confirmation can consume
    the OLDEST open lots first (true First-In-First-Out) instead of a single
    tenant-wide average.

    qty_remaining is decremented as lots are consumed (see
    sales_inventory.consume_fifo_for_item) and incremented back if the
    consuming order is cancelled/released (see release_fifo_for_item).
    Lots created before this feature shipped don't exist — stock received
    prior to this change has no lot history, so consumption for those units
    falls back to ProductStock.avg_cost (see consume_fifo_for_item's
    fallback branch, flagged via FifoConsumption.is_fallback).
    """
    __tablename__ = "stock_lots"
    id            = Column(String,  primary_key=True, default=new_id)
    tenant_id     = Column(String,  ForeignKey("tenants.id"), nullable=False)
    variant_id    = Column(String,  ForeignKey("product_variants.id"), nullable=False)
    po_id         = Column(String,  ForeignKey("inventory_purchase_orders.id"), nullable=True)
    unit_cost     = Column(Float,   nullable=False)
    qty_received  = Column(Float,   nullable=False)
    qty_remaining = Column(Float,   nullable=False)
    received_at   = Column(DateTime, default=datetime.utcnow)
    created_at    = Column(DateTime, default=datetime.utcnow)

    tenant  = relationship("Tenant")
    variant = relationship("ProductVariant")
    po      = relationship("InventoryPurchaseOrder")


class FifoConsumption(Base):
    """
    Records exactly which StockLot(s) — and how much of each — a SalesOrderItem
    drew on at reservation time. This is the audit trail behind Sales
    Insights' "how was this cost calculated?" breakdown, and behind
    SalesOrderItem.cost_snapshot (which is the qty-weighted average of these
    rows). lot_id is NULL when qty was covered by the pre-FIFO avg_cost
    fallback (is_fallback=True) rather than a tracked lot.
    """
    __tablename__ = "fifo_consumptions"
    id             = Column(String,  primary_key=True, default=new_id)
    tenant_id      = Column(String,  ForeignKey("tenants.id"), nullable=False)
    order_item_id  = Column(String,  ForeignKey("sales_order_items.id"), nullable=False)
    lot_id         = Column(String,  ForeignKey("stock_lots.id"), nullable=True)
    qty            = Column(Float,   nullable=False)
    unit_cost      = Column(Float,   nullable=False)
    is_fallback    = Column(Boolean, default=False)
    created_at     = Column(DateTime, default=datetime.utcnow)

    tenant     = relationship("Tenant")
    order_item = relationship("SalesOrderItem")
    lot        = relationship("StockLot")


class InventoryPurchaseOrder(Base):
    """
    Tracks incoming stock from vendors (in-transit).
    status lifecycle: DRAFT → SUBMITTED → APPROVED → PARTIALLY_RECEIVED → RECEIVED → CANCELLED
    expected_arrival_date: shown to sales agents when a product is out of stock.
    """
    __tablename__ = "inventory_purchase_orders"
    id                    = Column(String,  primary_key=True, default=new_id)
    tenant_id             = Column(String,  ForeignKey("tenants.id"), nullable=False)
    display_id            = Column(String,  nullable=True)         # PO-0001
    vendor_id             = Column(String,  ForeignKey("vendors.id"), nullable=True)
    vendor_name_snapshot  = Column(String,  nullable=True)
    status                = Column(String,  default="DRAFT")
    expected_arrival_date = Column(Date,    nullable=True)
    notes                 = Column(Text,    nullable=True)
    created_by_id         = Column(String,  ForeignKey("users.id"), nullable=True)
    approved_by_id        = Column(String,  ForeignKey("users.id"), nullable=True)
    is_deleted            = Column(Boolean, default=False)
    created_at            = Column(DateTime, default=datetime.utcnow)
    updated_at            = Column(DateTime, default=datetime.utcnow)

    tenant      = relationship("Tenant")
    vendor      = relationship("Vendor",  foreign_keys=[vendor_id])
    created_by  = relationship("User",    foreign_keys=[created_by_id])
    approved_by = relationship("User",    foreign_keys=[approved_by_id])
    items       = relationship("InventoryPOItem", back_populates="po",
                               cascade="all, delete-orphan")


class InventoryPOItem(Base):
    __tablename__ = "inventory_po_items"
    id           = Column(String, primary_key=True, default=new_id)
    po_id        = Column(String, ForeignKey("inventory_purchase_orders.id"), nullable=False)
    variant_id   = Column(String, ForeignKey("product_variants.id"), nullable=False)
    qty_ordered  = Column(Float,  nullable=False)
    qty_received = Column(Float,  default=0.0)
    unit_cost    = Column(Float,  nullable=True)
    unit_id      = Column(String, ForeignKey("units_of_measure.id"), nullable=True)

    po      = relationship("InventoryPurchaseOrder", back_populates="items")
    variant = relationship("ProductVariant")
    unit    = relationship("UnitOfMeasure", foreign_keys=[unit_id])


class PurchaseRequest(Base):
    """
    Sales-agent-raised "please reorder this" request, surfaced when a
    catalog product has zero available stock AND no open PO already covers
    it. Shows up in Inventory's Purchase Orders page for approval; approving
    one is the trigger to actually create a PurchaseOrder for it.
    status lifecycle: PENDING -> APPROVED | DISMISSED
    """
    __tablename__ = "purchase_requests"
    id              = Column(String,   primary_key=True, default=new_id)
    tenant_id       = Column(String,   ForeignKey("tenants.id"), nullable=False)
    variant_id      = Column(String,   ForeignKey("product_variants.id"), nullable=False)
    requested_by_id = Column(String,   ForeignKey("users.id"), nullable=False)
    qty_requested   = Column(Float,    nullable=True)
    notes           = Column(Text,     nullable=True)
    status          = Column(String,   default="PENDING")
    po_id           = Column(String,   ForeignKey("inventory_purchase_orders.id"), nullable=True)
    resolved_by_id  = Column(String,   ForeignKey("users.id"), nullable=True)
    resolved_at     = Column(DateTime, nullable=True)
    created_at      = Column(DateTime, default=datetime.utcnow)

    tenant       = relationship("Tenant")
    variant      = relationship("ProductVariant")
    requested_by = relationship("User", foreign_keys=[requested_by_id])
    resolved_by  = relationship("User", foreign_keys=[resolved_by_id])
    po           = relationship("InventoryPurchaseOrder", foreign_keys=[po_id])


class PriceList(Base):
    """
    Named price list (e.g. "Standard", "Wholesale", "Export") — Brief 06.
    One list must be is_default=True per tenant.
    Customers are assigned a price_list_id. Those without assignment fall back to default.
    """
    __tablename__ = "price_lists"
    id          = Column(String,  primary_key=True, default=new_id)
    tenant_id   = Column(String,  ForeignKey("tenants.id"), nullable=False)
    name        = Column(String,  nullable=False)
    description = Column(Text,    nullable=True)
    is_default  = Column(Boolean, default=False)
    valid_from  = Column(Date,    nullable=True)
    valid_to    = Column(Date,    nullable=True)
    is_active   = Column(Boolean, default=True)
    is_deleted  = Column(Boolean, default=False)
    created_by_id = Column(String, ForeignKey("users.id"), nullable=True)
    created_at  = Column(DateTime, default=datetime.utcnow)
    updated_at  = Column(DateTime, default=datetime.utcnow)

    tenant     = relationship("Tenant")
    created_by = relationship("User", foreign_keys=[created_by_id])
    items      = relationship("PriceListItem", back_populates="price_list",
                              cascade="all, delete-orphan")


class PriceListItem(Base):
    """One product's sell price within a named price list — Brief 06."""
    __tablename__ = "price_list_items"
    id            = Column(String,  primary_key=True, default=new_id)
    price_list_id = Column(String,  ForeignKey("price_lists.id"),  nullable=False)
    variant_id    = Column(String,  ForeignKey("product_variants.id"), nullable=False)
    tenant_id     = Column(String,  ForeignKey("tenants.id"),      nullable=False)
    unit_price    = Column(Float,   nullable=False)
    min_qty       = Column(Float,   default=0.0)
    is_active     = Column(Boolean, default=True)
    updated_at    = Column(DateTime, default=datetime.utcnow)

    price_list = relationship("PriceList", back_populates="items")
    variant    = relationship("ProductVariant")
    tenant     = relationship("Tenant")


class PriceListItemHistory(Base):
    """
    Append-only log of every price change in any price list — Brief 06.
    Written every time PriceListItem.unit_price is updated.
    Powers price trend charts in Brief 07.
    Never update or delete rows in this table.
    """
    __tablename__ = "price_list_item_history"
    id            = Column(String,  primary_key=True, default=new_id)
    price_list_id = Column(String,  ForeignKey("price_lists.id"),  nullable=False)
    price_list_name_snapshot = Column(String, nullable=True)
    variant_id    = Column(String,  ForeignKey("product_variants.id"), nullable=False)
    tenant_id     = Column(String,  ForeignKey("tenants.id"),      nullable=False)
    old_price     = Column(Float,   nullable=True)
    new_price     = Column(Float,   nullable=False)
    changed_by_id = Column(String,  ForeignKey("users.id"),        nullable=True)
    changed_at    = Column(DateTime, default=datetime.utcnow)

    price_list = relationship("PriceList")
    variant    = relationship("ProductVariant")
    tenant     = relationship("Tenant")
    changed_by = relationship("User", foreign_keys=[changed_by_id])


class CustomerPriceOverride(Base):
    """
    Per-customer, per-product price override — Brief 06.
    Takes priority over any price list entry during resolution.
    valid_from / valid_to: NULL means always valid.
    """
    __tablename__ = "customer_price_overrides"
    id           = Column(String, primary_key=True, default=new_id)
    tenant_id    = Column(String, ForeignKey("tenants.id"),   nullable=False)
    customer_id  = Column(String, ForeignKey("customers.id"), nullable=False)
    variant_id   = Column(String, ForeignKey("product_variants.id"), nullable=False)
    unit_price   = Column(Float,  nullable=False)
    valid_from   = Column(Date,   nullable=True)
    valid_to     = Column(Date,   nullable=True)
    reason       = Column(Text,   nullable=True)
    created_by_id= Column(String, ForeignKey("users.id"), nullable=True)
    is_active    = Column(Boolean, default=True)
    created_at   = Column(DateTime, default=datetime.utcnow)

    tenant     = relationship("Tenant")
    customer   = relationship("Customer", foreign_keys=[customer_id])
    variant    = relationship("ProductVariant")
    created_by = relationship("User", foreign_keys=[created_by_id])


class CostEntry(Base):
    """
    Immutable append-only log of buy-price and cost entries per product — Brief 06.
    Never update or delete rows.

    cost_type values:
      BUY_PRICE — purchase price from supplier
      FREIGHT   — inbound freight cost
      HANDLING  — handling or storage cost
      OTHER     — any other attributable cost

    The most recent BUY_PRICE entry for a product is the current buy price.
    All entries for a product on a given date aggregate to total landed cost.
    Powers price trend analytics (Brief 07).
    """
    __tablename__ = "cost_entries"
    id             = Column(String,  primary_key=True, default=new_id)
    tenant_id      = Column(String,  ForeignKey("tenants.id"),  nullable=False)
    variant_id     = Column(String,  ForeignKey("product_variants.id"), nullable=False)
    cost_type      = Column(String,  nullable=False)
    amount         = Column(Float,   nullable=False)
    effective_date = Column(Date,    nullable=False)
    notes          = Column(Text,    nullable=True)
    actor_id       = Column(String,  ForeignKey("users.id"), nullable=True)
    created_at     = Column(DateTime, default=datetime.utcnow)

    tenant  = relationship("Tenant")
    variant = relationship("ProductVariant")
    actor   = relationship("User", foreign_keys=[actor_id])


class SalesOrder(Base):
    """
    Order header — Brief 05.
    status lifecycle:
      DRAFT       — agent is building the order. No stock reserved yet.
      CONFIRMED   — agent finalised. Stock reservation triggered per line item.
      DISPATCHED  — godown has shipped goods.
      DELIVERED   — customer confirmed receipt.
      CANCELLED   — order cancelled. All reservations released automatically.

    price_list_id_snapshot: the resolved price list at confirmation (audit trail).
    gross_margin_pct: computed at confirmation from line item margins.
    """
    __tablename__ = "sales_orders"
    id                     = Column(String,  primary_key=True, default=new_id)
    display_id             = Column(String,  nullable=True)        # SO-0001
    tenant_id              = Column(String,  ForeignKey("tenants.id"),   nullable=False)
    customer_id            = Column(String,  ForeignKey("customers.id"), nullable=False)
    agent_id               = Column(String,  ForeignKey("users.id"),     nullable=False)
    status                 = Column(String,  default="DRAFT")
    payment_terms          = Column(String,  nullable=True)
    delivery_address       = Column(Text,    nullable=True)
    branch_id              = Column(String,  ForeignKey("branches.id"), nullable=True)
    expected_delivery_date = Column(Date,    nullable=True)
    notes                  = Column(Text,    nullable=True)
    call_log_id            = Column(String,  nullable=True)
    price_list_id_snapshot = Column(String,  nullable=True)
    total_amount           = Column(Float,   default=0.0)
    total_cost              = Column(Float,   default=0.0)
    gross_margin_pct       = Column(Float,   nullable=True)
    confirmed_at           = Column(DateTime, nullable=True)
    dispatched_at          = Column(DateTime, nullable=True)
    delivered_at           = Column(DateTime, nullable=True)
    cancelled_at           = Column(DateTime, nullable=True)
    cancellation_reason    = Column(Text,    nullable=True)
    is_deleted              = Column(Boolean, default=False)
    created_at             = Column(DateTime, default=datetime.utcnow)
    updated_at             = Column(DateTime, default=datetime.utcnow)
    # Dispatch Queue manual sequencing (lower = ships first). Set when an
    # order becomes CONFIRMED (appended to the end of the queue), reassigned
    # in bulk by the queue's drag-and-drop reorder endpoint, cleared once the
    # order is fully DISPATCHED (it leaves the queue).
    dispatch_priority      = Column(Integer, nullable=True)

    tenant   = relationship("Tenant")
    customer = relationship("Customer",  foreign_keys=[customer_id])
    agent    = relationship("User",      foreign_keys=[agent_id])
    branch   = relationship("Branch",    foreign_keys=[branch_id])
    items    = relationship("SalesOrderItem", back_populates="order",
                            cascade="all, delete-orphan",
                            order_by="SalesOrderItem.created_at")


class SalesOrderItem(Base):
    """
    One line item in a sales order — Brief 05.

    price_source values (populated at confirmation):
      CUSTOMER_OVERRIDE — a customer-specific price was active
      PRICE_LIST        — resolved from customer's assigned price list
      DEFAULT_LIST      — resolved from the tenant's default price list
      MANUAL            — agent entered a custom price
      NONE              — no price found (order cannot confirm in this state)

    approval_status: used when manual price is below SALES_MARGIN_FLOOR_PCT.
      PENDING | APPROVED | REJECTED

    cost_snapshot: buy price (avg_cost from product_stock) at confirmation time.
      Used for margin calculation. Immutable after confirmation.

    stock_status (set at confirmation):
      AVAILABLE   — full qty reserved
      PARTIAL     — partial qty reserved, rest is in-transit
      UNAVAILABLE — no stock and no in-transit
    """
    __tablename__ = "sales_order_items"
    id                    = Column(String,  primary_key=True, default=new_id)
    order_id              = Column(String,  ForeignKey("sales_orders.id"),     nullable=False)
    tenant_id             = Column(String,  ForeignKey("tenants.id"),          nullable=False)
    variant_id            = Column(String,  ForeignKey("product_variants.id"), nullable=False)
    qty_ordered           = Column(Float,   nullable=False)
    unit_id               = Column(String,  ForeignKey("units_of_measure.id"), nullable=True)
    unit_price            = Column(Float,   nullable=False)
    price_source          = Column(String,  nullable=True)
    manual_override_price = Column(Float,   nullable=True)
    override_reason       = Column(Text,    nullable=True)
    approval_status       = Column(String,  nullable=True)
    cost_snapshot         = Column(Float,   nullable=True)
    # 2026-07 FIFO redesign: cost_snapshot above is now auto-computed as the
    # qty-weighted average of the FifoConsumption rows for this item (see
    # sales_inventory.consume_fifo_for_item). cost_snapshot_override lets a
    # user correct that auto value for GP reporting without touching actual
    # stock lots/balances — when set, Sales Insights uses it instead of
    # cost_snapshot everywhere.
    cost_snapshot_override = Column(Float,    nullable=True)
    cost_override_note     = Column(Text,     nullable=True)
    cost_override_by_id    = Column(String,   ForeignKey("users.id"), nullable=True)
    cost_override_at       = Column(DateTime, nullable=True)
    qty_dispatched        = Column(Float,   default=0.0)
    line_total            = Column(Float,   default=0.0)
    stock_status          = Column(String,  nullable=True)
    in_transit_arrival    = Column(Date,    nullable=True)
    created_at            = Column(DateTime, default=datetime.utcnow)

    order   = relationship("SalesOrder",    back_populates="items")
    variant = relationship("ProductVariant")
    unit    = relationship("UnitOfMeasure", foreign_keys=[unit_id])
    tenant  = relationship("Tenant")
    media   = relationship(
        "MediaUpload",
        primaryjoin="and_(MediaUpload.entity_type=='sales_order_item', foreign(MediaUpload.entity_id)==SalesOrderItem.id)",
        viewonly=True,
    )


class StockReservation(Base):
    """
    Explicit reservation record — Brief 05. Drives the concurrency-safe booking.

    status values:
      ACTIVE    — stock is held for this order
      FULFILLED — order dispatched; reservation converted to STOCK_OUT
      RELEASED  — order cancelled or expired; stock returned to available

    expires_at: scheduler auto-releases ACTIVE reservations past this time.
                Prevents indefinitely locked stock on abandoned orders.
    """
    __tablename__ = "stock_reservations"
    id             = Column(String,  primary_key=True, default=new_id)
    tenant_id      = Column(String,  ForeignKey("tenants.id"),       nullable=False)
    variant_id     = Column(String,  ForeignKey("product_variants.id"), nullable=False)
    order_id       = Column(String,  ForeignKey("sales_orders.id"),  nullable=False)
    order_item_id  = Column(String,  ForeignKey("sales_order_items.id"), nullable=True)
    qty_reserved   = Column(Float,   nullable=False)
    status         = Column(String,  default="ACTIVE")
    reserved_by_id = Column(String,  ForeignKey("users.id"), nullable=False)
    reserved_at    = Column(DateTime, default=datetime.utcnow)
    expires_at     = Column(DateTime, nullable=True)
    fulfilled_at   = Column(DateTime, nullable=True)
    released_at    = Column(DateTime, nullable=True)
    release_reason = Column(Text,    nullable=True)

    tenant      = relationship("Tenant")
    variant     = relationship("ProductVariant")
    order       = relationship("SalesOrder",    foreign_keys=[order_id])
    order_item  = relationship("SalesOrderItem", foreign_keys=[order_item_id])
    reserved_by = relationship("User",          foreign_keys=[reserved_by_id])


class SalesTarget(Base):
    """
    Admin/Manager-set revenue (and optional order-count) target per agent
    per period — Phase 2. Actuals are computed on read from SalesOrder
    (status in CONFIRMED/DISPATCHED/DELIVERED), not stored redundantly here.

    period_label: e.g. "2026-07" for a monthly target.
    """
    __tablename__ = "sales_targets"
    id             = Column(String,  primary_key=True, default=new_id)
    tenant_id      = Column(String,  ForeignKey("tenants.id"), nullable=False)
    agent_id       = Column(String,  ForeignKey("users.id"),   nullable=False)
    period_label   = Column(String,  nullable=False)
    target_amount  = Column(Float,   nullable=False)
    target_orders  = Column(Integer, nullable=True)
    created_by_id  = Column(String,  ForeignKey("users.id"), nullable=True)
    created_at     = Column(DateTime, default=datetime.utcnow)

    tenant     = relationship("Tenant")
    agent      = relationship("User", foreign_keys=[agent_id])
    created_by = relationship("User", foreign_keys=[created_by_id])


class SalesTargetHistory(Base):
    """
    Append-only log of every SalesTarget create/update.
    Written every time a target is set via /sales/orders/targets/set.
    Never update or delete rows in this table.
    """
    __tablename__ = "sales_target_history"
    id                 = Column(String,  primary_key=True, default=new_id)
    tenant_id          = Column(String,  ForeignKey("tenants.id"), nullable=False)
    agent_id           = Column(String,  ForeignKey("users.id"),   nullable=False)
    period_label       = Column(String,  nullable=False)
    old_target_amount  = Column(Float,   nullable=True)
    new_target_amount  = Column(Float,   nullable=False)
    old_target_orders  = Column(Integer, nullable=True)
    new_target_orders  = Column(Integer, nullable=True)
    changed_by_id      = Column(String,  ForeignKey("users.id"), nullable=True)
    changed_at         = Column(DateTime, default=datetime.utcnow)

    tenant     = relationship("Tenant")
    agent      = relationship("User", foreign_keys=[agent_id])
    changed_by = relationship("User", foreign_keys=[changed_by_id])


def create_tables():
    Base.metadata.create_all(bind=engine)
    # Run any pending Alembic migrations on startup
    try:
        from alembic.config import Config
        from alembic import command
        alembic_cfg = Config("alembic.ini")
        command.upgrade(alembic_cfg, "head")
    except Exception as e:
        import logging
        logging.getLogger(__name__).error("Alembic upgrade FAILED — DB schema may be behind models: %s", e)
    # PostgreSQL column additions — must run before any seed that queries these columns
    _pg_add_columns()
    # Auto-migrate: add any columns present in models but missing from the DB (SQLite)
    try:
        import sys, os
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from migrate import run_migrations
        run_migrations()
    except Exception as _e:
        import logging
        logging.getLogger(__name__).warning("migrate.py skipped: %s", _e)
    _seed_builtin_submodules()


def _pg_add_columns():
    """Add new columns to existing PostgreSQL tables that predate the model changes.
    SQLite gets these via create_all / migrate.py — skip it here."""
    import logging
    from sqlalchemy import text as _text
    _log = logging.getLogger(__name__)
    with engine.connect() as _probe:
        if _probe.dialect.name != "postgresql":
            return
    _migrations = [
        # Flow Board label columns — TenantLabelConfig
        "ALTER TABLE tenant_label_configs ADD COLUMN IF NOT EXISTS fms_s VARCHAR",
        "ALTER TABLE tenant_label_configs ADD COLUMN IF NOT EXISTS fms_p VARCHAR",
        # Flow Board label columns — LibraryLabelBundle
        "ALTER TABLE library_label_bundles ADD COLUMN IF NOT EXISTS fms_s VARCHAR",
        "ALTER TABLE library_label_bundles ADD COLUMN IF NOT EXISTS fms_p VARCHAR",
        # Login tracking
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS last_login TIMESTAMP",
        # E-14: custom checklist frequency
        "ALTER TABLE checklist_templates ADD COLUMN IF NOT EXISTS frequency_type VARCHAR",
        "ALTER TABLE checklist_templates ADD COLUMN IF NOT EXISTS frequency_config JSONB",
        # Sales foundation — module access on users
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS module_access_json TEXT DEFAULT '[]'",
        # Sales foundation — units of measure table
        """CREATE TABLE IF NOT EXISTS units_of_measure (
            id VARCHAR PRIMARY KEY,
            tenant_id VARCHAR REFERENCES tenants(id),
            name VARCHAR NOT NULL,
            abbreviation VARCHAR NOT NULL,
            is_active BOOLEAN DEFAULT TRUE,
            is_deleted BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT NOW()
        )""",
        # Performance formula (new table — CREATE IF NOT EXISTS is safe to repeat)
        """CREATE TABLE IF NOT EXISTS performance_formulas (
            id VARCHAR PRIMARY KEY,
            tenant_id VARCHAR REFERENCES tenants(id),
            label VARCHAR,
            weights JSONB NOT NULL,
            is_active BOOLEAN DEFAULT TRUE,
            created_at TIMESTAMP DEFAULT NOW(),
            created_by_id VARCHAR REFERENCES users(id)
        )""",
        # CRM contacts (Brief 04) — customer extensions
        "ALTER TABLE customers ADD COLUMN IF NOT EXISTS assigned_agent_id VARCHAR REFERENCES users(id)",
        "ALTER TABLE customers ADD COLUMN IF NOT EXISTS customer_tier VARCHAR DEFAULT 'UNRANKED'",
        "ALTER TABLE customers ADD COLUMN IF NOT EXISTS last_contacted_at TIMESTAMP",
        "ALTER TABLE customers ADD COLUMN IF NOT EXISTS contact_freq_days INTEGER DEFAULT 30",
        "ALTER TABLE customers ADD COLUMN IF NOT EXISTS price_list_id VARCHAR",
        "ALTER TABLE customers ADD COLUMN IF NOT EXISTS gstin VARCHAR",
        "ALTER TABLE customers ADD COLUMN IF NOT EXISTS credit_limit FLOAT",
        "ALTER TABLE customers ADD COLUMN IF NOT EXISTS billing_address TEXT",
        "ALTER TABLE customers ADD COLUMN IF NOT EXISTS shipping_address TEXT",
        # CRM call logs (Brief 04)
        """CREATE TABLE IF NOT EXISTS crm_call_logs (
            id VARCHAR PRIMARY KEY,
            tenant_id VARCHAR NOT NULL REFERENCES tenants(id),
            customer_id VARCHAR NOT NULL REFERENCES customers(id),
            agent_id VARCHAR NOT NULL REFERENCES users(id),
            contacted_at TIMESTAMP DEFAULT NOW(),
            outcome VARCHAR NOT NULL,
            follow_up_at TIMESTAMP,
            follow_up_done BOOLEAN DEFAULT FALSE,
            order_id VARCHAR,
            notes TEXT,
            created_at TIMESTAMP DEFAULT NOW()
        )""",
        """CREATE INDEX IF NOT EXISTS idx_call_logs_agent_followup
            ON crm_call_logs(agent_id, follow_up_at, follow_up_done)
            WHERE follow_up_done = FALSE""",
        # Phase 2 — Orders/Contacts/Pricing brief
        "ALTER TABLE customers ADD COLUMN IF NOT EXISTS default_payment_terms VARCHAR",
        "ALTER TABLE sales_orders ADD COLUMN IF NOT EXISTS branch_id VARCHAR REFERENCES branches(id)",
        """CREATE TABLE IF NOT EXISTS sales_targets (
            id VARCHAR PRIMARY KEY,
            tenant_id VARCHAR NOT NULL REFERENCES tenants(id),
            agent_id VARCHAR NOT NULL REFERENCES users(id),
            period_label VARCHAR NOT NULL,
            target_amount FLOAT NOT NULL,
            target_orders INTEGER,
            created_by_id VARCHAR REFERENCES users(id),
            created_at TIMESTAMP DEFAULT NOW()
        )""",
        # Product catalog hierarchy — End Products / Variants / per-branch stock
        # (alembic e1p2r3o4d5c6 / b2r4nch5t0ck stalled behind the pre-fix
        # d1sp4tch5qu6u migration on Postgres, so these never landed — see
        # bulk-upload "Internal Server Error" investigation)
        "ALTER TABLE end_products ADD COLUMN IF NOT EXISTS category_id VARCHAR REFERENCES categories(id)",
        "ALTER TABLE end_products ADD COLUMN IF NOT EXISTS sub_category_id VARCHAR REFERENCES sub_categories(id)",
        "ALTER TABLE product_variants ADD COLUMN IF NOT EXISTS end_product_id VARCHAR REFERENCES end_products(id)",
        "ALTER TABLE product_variants ADD COLUMN IF NOT EXISTS product_tier VARCHAR DEFAULT 'UNRANKED'",
        "ALTER TABLE product_variants ADD COLUMN IF NOT EXISTS low_stock_threshold FLOAT",
        "ALTER TABLE product_variants ADD COLUMN IF NOT EXISTS media_urls_json TEXT DEFAULT '[]'",
        "ALTER TABLE product_stock ADD COLUMN IF NOT EXISTS branch_id VARCHAR REFERENCES branches(id)",
        # Legacy pre-rename columns (product_id -> variant_id, migration
        # g1h2i3j4k5l6) left over from the same stalled migration chain — the
        # ORM never writes product_id anymore, so drop the NOT NULL
        # constraint on each rather than leave every insert failing.
        "ALTER TABLE product_stock ALTER COLUMN product_id DROP NOT NULL",
        "ALTER TABLE stock_ledger ALTER COLUMN product_id DROP NOT NULL",
        "ALTER TABLE inventory_po_items ALTER COLUMN product_id DROP NOT NULL",
        "ALTER TABLE price_list_items ALTER COLUMN product_id DROP NOT NULL",
        "ALTER TABLE price_list_item_history ALTER COLUMN product_id DROP NOT NULL",
        "ALTER TABLE customer_price_overrides ALTER COLUMN product_id DROP NOT NULL",
        "ALTER TABLE cost_entries ALTER COLUMN product_id DROP NOT NULL",
        "ALTER TABLE sales_order_items ALTER COLUMN product_id DROP NOT NULL",
        "ALTER TABLE stock_reservations ALTER COLUMN product_id DROP NOT NULL",
        # Setup > Notifications > WhatsApp — per-event send toggles
        "ALTER TABLE tenants ADD COLUMN IF NOT EXISTS wa_notif_ticket_assigned BOOLEAN DEFAULT TRUE",
        "ALTER TABLE tenants ADD COLUMN IF NOT EXISTS wa_notif_ticket_escalated BOOLEAN DEFAULT TRUE",
        "ALTER TABLE tenants ADD COLUMN IF NOT EXISTS wa_notif_fms_ticket_created BOOLEAN DEFAULT TRUE",
        "ALTER TABLE tenants ADD COLUMN IF NOT EXISTS wa_notif_fms_stage_transition BOOLEAN DEFAULT TRUE",
        "ALTER TABLE tenants ADD COLUMN IF NOT EXISTS wa_notif_order_placed BOOLEAN DEFAULT TRUE",
        "ALTER TABLE tenants ADD COLUMN IF NOT EXISTS wa_notif_order_dispatched BOOLEAN DEFAULT TRUE",
        "ALTER TABLE tenants ADD COLUMN IF NOT EXISTS wa_notif_ticket_closed BOOLEAN DEFAULT TRUE",
        "ALTER TABLE tenants ADD COLUMN IF NOT EXISTS wa_notif_ticket_tat_reminder BOOLEAN DEFAULT TRUE",
        "ALTER TABLE tenants ADD COLUMN IF NOT EXISTS wa_notif_fms_ticket_closed BOOLEAN DEFAULT TRUE",
        "ALTER TABLE tenants ADD COLUMN IF NOT EXISTS wa_notif_fms_ticket_flagged BOOLEAN DEFAULT TRUE",
        "ALTER TABLE tenants ADD COLUMN IF NOT EXISTS wa_notif_po_placed BOOLEAN DEFAULT TRUE",
        "ALTER TABLE tenants ADD COLUMN IF NOT EXISTS wa_notif_po_accepted BOOLEAN DEFAULT TRUE",
        # Employee's own WhatsApp on/off preference (Employees tab)
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS whatsapp_notifications_enabled BOOLEAN DEFAULT TRUE",
        # FMS: restrict ticket actions to the current-stage assignee only
        "ALTER TABLE fms_flows ADD COLUMN IF NOT EXISTS restrict_to_assignee BOOLEAN DEFAULT FALSE",
        # Checklists: due date vs due time distinction, per template
        "ALTER TABLE checklist_templates ADD COLUMN IF NOT EXISTS due_time_mode VARCHAR DEFAULT 'ANYTIME'",
        "ALTER TABLE checklist_templates ADD COLUMN IF NOT EXISTS due_time VARCHAR",
    ]
    try:
        with engine.begin() as conn:
            for stmt in _migrations:
                try:
                    conn.execute(_text(stmt))
                except Exception as col_err:
                    _log.warning("Column migration skipped (%s): %s", stmt, col_err)
    except Exception as e:
        _log.warning("_pg_add_columns skipped: %s", e)


def seed_default_uoms(db, tenant_id: str) -> None:
    """Seed default units of measure for a new tenant if none exist yet."""
    existing = db.query(UnitOfMeasure).filter(
        UnitOfMeasure.tenant_id == tenant_id,
        UnitOfMeasure.is_deleted == False,
    ).count()
    if existing:
        return
    for uom in _DEFAULT_UOMS:
        db.add(UnitOfMeasure(
            id=new_id(), tenant_id=tenant_id,
            name=uom["name"], abbreviation=uom["abbreviation"],
        ))


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
]


def _seed_builtin_submodules():
    """Ensure the 3 built-in system sub-module records exist in the library.
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


class TierSnapshot(Base):
    """
    Weekly computed tier for a customer or product — Brief 07.
    entity_type: CUSTOMER | PRODUCT
    tier: A | B | C | D | UNRANKED
    period_label: ISO week string e.g. "W2026-26"
    basis_json: the raw metrics used to compute the tier (for audit/display).
    """
    __tablename__ = "tier_snapshots"
    id           = Column(String,  primary_key=True, default=new_id)
    tenant_id    = Column(String,  ForeignKey("tenants.id"), nullable=False)
    entity_type  = Column(String,  nullable=False)
    entity_id    = Column(String,  nullable=False)
    tier         = Column(String,  nullable=False)
    score        = Column(Float,   nullable=True)
    basis_json   = Column(Text,    nullable=True)
    period_label = Column(String,  nullable=False)
    computed_at  = Column(DateTime, default=datetime.utcnow)

    tenant = relationship("Tenant")


class AnomalyAlert(Base):
    """
    AI-detected anomaly surfaced to Managers and Admins — Brief 07.

    alert_type values:
      PRICE_SPIKE         — buy price up >15% vs 30-day baseline
      MARGIN_DROP         — gross margin down >10 pts vs 4-week average
      CUSTOMER_DROPOUT    — tier-A or tier-B customer with no orders in 45 days
      LOW_STOCK           — tier-A product below low_stock_threshold
      AGENT_NEGLECT       — agent with zero call logs in 7 days
      ORDER_CANCEL_SPIKE  — cancellations this week > 2x prior week

    severity: LOW | MEDIUM | HIGH | CRITICAL
    detail: plain-English description generated by Claude API.
    metric_json: raw numbers used to detect the anomaly (for transparency).
    """
    __tablename__ = "anomaly_alerts"
    id            = Column(String,  primary_key=True, default=new_id)
    tenant_id     = Column(String,  ForeignKey("tenants.id"), nullable=False)
    alert_type    = Column(String,  nullable=False)
    entity_type   = Column(String,  nullable=True)
    entity_id     = Column(String,  nullable=True)
    entity_label  = Column(String,  nullable=True)
    severity      = Column(String,  default="MEDIUM")
    detail        = Column(Text,    nullable=True)
    metric_json   = Column(Text,    nullable=True)
    is_read       = Column(Boolean, default=False)
    is_dismissed  = Column(Boolean, default=False)
    detected_at   = Column(DateTime, default=datetime.utcnow)

    tenant = relationship("Tenant")
