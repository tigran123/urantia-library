"""Router module (extracted from main.py): per-user notification preferences.
Shares `_user_notif_prefs` with the feedback router. Moved verbatim from
main.py (no logic change)."""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

import models
import schemas
from database import get_db
from config import _now_iso
from deps import get_current_user
from routers.feedback import _user_notif_prefs

router = APIRouter()


# ---------- User: notification prefs ----------

@router.get("/api/users/me/notification-prefs")
async def get_my_notification_prefs(
    current_user: models.User = Depends(get_current_user), db: Session = Depends(get_db),
):
    p = _user_notif_prefs(db, current_user.id)
    return {
        "email_on_reply": bool(p.email_on_reply),
        "email_on_status": bool(p.email_on_status),
        "email_weekly_summary": bool(p.email_weekly_summary),
    }


@router.put("/api/users/me/notification-prefs")
async def set_my_notification_prefs(
    payload: schemas.NotificationPrefs,
    current_user: models.User = Depends(get_current_user), db: Session = Depends(get_db),
):
    p = _user_notif_prefs(db, current_user.id)
    p.email_on_reply = payload.email_on_reply
    p.email_on_status = payload.email_on_status
    p.email_weekly_summary = payload.email_weekly_summary
    p.updated_at = _now_iso()
    db.commit()
    return {"ok": True}
