"""Router module (extracted from main.py): per-user reading progress.
Guest-reachable (get_optional_user); guests get a no-op response. Moved verbatim
from main.py (no logic change)."""
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session

import models
import schemas
from database import get_db
from deps import get_optional_user, _clearance_of, _is_admin
from paths import _book_clearance

router = APIRouter()


@router.get("/api/progress/{hash_id}", response_model=schemas.ReadingProgressResponse)
async def get_progress(hash_id: str, current_user: models.User | None = Depends(get_optional_user), db: Session = Depends(get_db)):
    if current_user is None:  # guest — no saved progress
        raise HTTPException(status_code=404, detail="No progress found")
    if not _is_admin(current_user) and _book_clearance(hash_id, db) > _clearance_of(current_user):
        raise HTTPException(status_code=403, detail="Forbidden")
    progress = db.query(models.ReadingProgress).filter(
        models.ReadingProgress.user_id == current_user.id,
        models.ReadingProgress.hash_id == hash_id
    ).first()
    if not progress:
        raise HTTPException(status_code=404, detail="No progress found")
    return progress

@router.post("/api/progress", response_model=schemas.ReadingProgressResponse)
async def update_progress(prog: schemas.ReadingProgressCreate, current_user: models.User | None = Depends(get_optional_user), db: Session = Depends(get_db)):
    pct = prog.percent
    if pct is not None:
        pct = max(0.0, min(1.0, float(pct)))
    if current_user is None:  # guest — accept silently, nothing is persisted
        return {"id": 0, "user_id": 0, "hash_id": prog.hash_id, "location": prog.location, "percent": pct}
    if not _is_admin(current_user) and _book_clearance(prog.hash_id, db) > _clearance_of(current_user):
        raise HTTPException(status_code=403, detail="Forbidden")
    existing = db.query(models.ReadingProgress).filter(
        models.ReadingProgress.user_id == current_user.id,
        models.ReadingProgress.hash_id == prog.hash_id
    ).first()
    if existing:
        existing.location = prog.location
        if pct is not None:
            existing.percent = pct
        db.commit()
        db.refresh(existing)
        return existing

    new_prog = models.ReadingProgress(user_id=current_user.id, hash_id=prog.hash_id, location=prog.location, percent=pct)
    db.add(new_prog)
    db.commit()
    db.refresh(new_prog)
    return new_prog
