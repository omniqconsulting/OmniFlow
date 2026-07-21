"""Notifications — /api/v1/notifications. Native counterpart of the
website's /notifications centre (app/main.py, app/templates/notifications.html):
same Notification rows, same mark-read/mark-all-read/unread-count semantics,
so read state stays in sync between web and app for the same user. Adds a
`day` bucket (today/earlier, IST) and a resolved `link_type`/`link_id` the
design's swipeable list needs — computed here rather than duplicated per
notif_type on the client.
"""
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from sqlalchemy.orm import Session

from ..database import Notification, User, get_db
from ..notifications import resolve_notification_link
from .pagination import paginate_cursor
from .schemas import Page
from .security import get_current_api_user

router = APIRouter(prefix="/notifications", tags=["Notifications"])

IST = timezone(timedelta(hours=5, minutes=30))

# notif_type -> (icon, category). Category matches the app's existing
# CATEGORY_COLOR palette (roleNav.ts) so a notification tile visually
# matches the module it's about.
_TYPE_META: dict[str, tuple[str, str]] = {
    "TICKET_ESCALATION": ("🎫", "op"),
    "TICKET_REMINDER": ("🎫", "op"),
    "TICKET_STATUS_CHANGED": ("🎫", "op"),
    "TICKET_FLAGGED": ("🚩", "op"),
    "CHECKLIST_OVERDUE": ("✅", "op"),
    "CHECKLIST_REMINDER": ("✅", "op"),
    "FMS_REMINDER": ("🔀", "op"),
    "FMS_BACKWARD_MOVE": ("🔀", "op"),
    "FMS_HELP_NEEDED": ("🔀", "op"),
    "ORDER_PLACED": ("📦", "sales"),
    "ORDER_DISPATCHED": ("📦", "sales"),
    "STOCK_UPDATED": ("📦", "sales"),
    "LOW_STOCK_ALERT": ("📦", "sales"),
    "FOLLOW_UP_REMINDER": ("🤝", "crm"),
    "AGENT_FOLLOWUP_OVERDUE": ("🤝", "crm"),
}
_DEFAULT_META = ("🔔", "op")


def _rel_time(when: datetime) -> str:
    seconds = max(0, (datetime.utcnow() - when).total_seconds())
    if seconds < 60:
        return "Just now"
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"{minutes}m ago"
    hours = int(minutes // 60)
    if hours < 24:
        return f"{hours}h ago"
    days = int(hours // 24)
    return f"{days}d ago"


class NotificationOut(BaseModel):
    id: str
    icon: str
    cat: str
    title: str
    body: Optional[str]
    meta: str
    rel: str
    day: str
    is_read: bool
    link_type: str
    link_id: Optional[str]


def _to_out(n: Notification) -> NotificationOut:
    icon, cat = _TYPE_META.get(n.notif_type, _DEFAULT_META)
    link_type, link_id = resolve_notification_link(n.link)
    created_ist = n.created_at.replace(tzinfo=timezone.utc).astimezone(IST)
    today_ist = datetime.now(timezone.utc).astimezone(IST).date()
    day = "today" if created_ist.date() == today_ist else "earlier"
    return NotificationOut(
        id=n.id, icon=icon, cat=cat, title=n.title, body=n.body,
        meta=created_ist.strftime("%d %b, %H:%M"), rel=_rel_time(n.created_at),
        day=day, is_read=n.is_read, link_type=link_type, link_id=link_id,
    )


@router.get("", response_model=Page[NotificationOut])
def list_notifications(
    cursor: Optional[str] = Query(None),
    limit: int = Query(30, ge=1, le=100),
    user: User = Depends(get_current_api_user),
    db: Session = Depends(get_db),
):
    q = db.query(Notification).filter(Notification.user_id == user.id)
    rows, next_cursor = paginate_cursor(q, Notification, cursor, limit)
    return Page(items=[_to_out(n) for n in rows], next_cursor=next_cursor)


class UnreadCountOut(BaseModel):
    unread: int


@router.get("/unread-count", response_model=UnreadCountOut)
def unread_count(user: User = Depends(get_current_api_user), db: Session = Depends(get_db)):
    count = db.query(Notification).filter(Notification.user_id == user.id, Notification.is_read == False).count()
    return UnreadCountOut(unread=count)


@router.post("/{notif_id}/read", response_model=NotificationOut)
def mark_read(notif_id: str, user: User = Depends(get_current_api_user), db: Session = Depends(get_db)):
    n = db.query(Notification).filter(Notification.id == notif_id, Notification.user_id == user.id).first()
    if not n:
        raise HTTPException(status_code=404, detail="Notification not found")
    n.is_read = True
    db.commit()
    db.refresh(n)
    return _to_out(n)


@router.post("/read-all", status_code=204)
def mark_all_read(user: User = Depends(get_current_api_user), db: Session = Depends(get_db)):
    db.query(Notification).filter(Notification.user_id == user.id, Notification.is_read == False).update({"is_read": True})
    db.commit()
    return None


@router.delete("/{notif_id}", status_code=204)
def delete_notification(notif_id: str, user: User = Depends(get_current_api_user), db: Session = Depends(get_db)):
    n = db.query(Notification).filter(Notification.id == notif_id, Notification.user_id == user.id).first()
    if n:
        db.delete(n)
        db.commit()
    return None
