"""Router module (extracted from main.py): the admin bulk-integrity job
subsystem — single-book verify plus the in-process job runner (start / list /
get / cancel). Job state lives in `state.INTEGRITY_JOBS`; only one job runs at a
time. Moved verbatim from main.py (no logic change)."""
import uuid
import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session

import models
import schemas
from database import get_db, SessionLocal
from config import _now_iso
from deps import require_admin
from cas import _verify_book_sync
from state import (
    INTEGRITY_JOBS, INTEGRITY_JOBS_LOCK, INTEGRITY_JOB_TTL_SECONDS,
    INTEGRITY_ALL_RESULTS_CAP,
)

router = APIRouter()


@router.post("/api/admin/integrity/verify/{hash_id}", response_model=schemas.IntegrityCheckResult)
async def admin_verify_book(
    hash_id: str,
    mode: str = "quick",
    _admin: models.User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    if mode not in ("quick", "full"):
        raise HTTPException(status_code=400, detail="mode must be 'quick' or 'full'")
    return await asyncio.to_thread(_verify_book_sync, hash_id, mode, db)


# ---------- Bulk integrity jobs ----------



def _sweep_expired_jobs() -> None:
    now = datetime.now(timezone.utc)
    with INTEGRITY_JOBS_LOCK:
        for jid in list(INTEGRITY_JOBS.keys()):
            job = INTEGRITY_JOBS[jid]
            fin = job.get("finished_at")
            if not fin or job["status"] == "running":
                continue
            try:
                finished = datetime.strptime(fin, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            except ValueError:
                continue
            if (now - finished).total_seconds() > INTEGRITY_JOB_TTL_SECONDS:
                del INTEGRITY_JOBS[jid]


def _running_job_id() -> Optional[str]:
    with INTEGRITY_JOBS_LOCK:
        for jid, job in INTEGRITY_JOBS.items():
            if job["status"] == "running":
                return jid
    return None


def _job_summary(job: dict) -> dict:
    return {
        "job_id": job["job_id"],
        "status": job["status"],
        "mode": job["mode"],
        "total": job["total"],
        "processed": job["processed"],
        "ok_count": job["ok_count"],
        "fail_count": job["fail_count"],
        "started_at": job["started_at"],
        "finished_at": job.get("finished_at"),
        "error": job.get("error"),
    }


async def _run_integrity_job(job_id: str) -> None:
    """Background worker. One book at a time, each in its own thread + session."""
    with INTEGRITY_JOBS_LOCK:
        job = INTEGRITY_JOBS.get(job_id)
    if job is None:
        return
    hash_ids = job["hash_ids"]
    mode = job["mode"]

    def _verify_one(hid: str) -> dict:
        session = SessionLocal()
        try:
            return _verify_book_sync(hid, mode, session)
        finally:
            session.close()

    try:
        for hid in hash_ids:
            if job.get("cancel_requested"):
                with INTEGRITY_JOBS_LOCK:
                    job["status"] = "cancelled"
                    job["finished_at"] = _now_iso()
                return
            try:
                result = await asyncio.to_thread(_verify_one, hid)
            except Exception as e:
                logging.exception("verify failed for %s", hid)
                result = {
                    "hash_id": hid, "mode": mode, "ok": False, "error": "exception",
                    "checks": [{"name": "worker", "ok": False, "detail": str(e)}],
                    "verified_at": _now_iso(),
                    "title": None, "original_filename": None, "db_update_failed": True,
                }
            with INTEGRITY_JOBS_LOCK:
                job["processed"] += 1
                if result["ok"]:
                    job["ok_count"] += 1
                else:
                    job["fail_count"] += 1
                    job["failures"].append(result)
                if len(job["all_results"]) < INTEGRITY_ALL_RESULTS_CAP:
                    job["all_results"].append(result)
                else:
                    job["all_results_truncated"] = True
        with INTEGRITY_JOBS_LOCK:
            if job["status"] == "running":
                job["status"] = "done"
                job["finished_at"] = _now_iso()
    except Exception as e:
        logging.exception("integrity job %s crashed", job_id)
        with INTEGRITY_JOBS_LOCK:
            job["status"] = "error"
            job["error"] = str(e)
            job["finished_at"] = _now_iso()


@router.post("/api/admin/integrity/jobs", response_model=schemas.IntegrityJobSummary)
async def admin_start_integrity_job(
    payload: schemas.IntegrityJobCreate,
    _admin: models.User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    _sweep_expired_jobs()
    if payload.scope not in ("all", "hash_ids"):
        raise HTTPException(status_code=400, detail="scope must be 'all' or 'hash_ids'")
    if payload.mode not in ("quick", "full"):
        raise HTTPException(status_code=400, detail="mode must be 'quick' or 'full'")

    if payload.scope == "all":
        hash_ids = [r[0] for r in db.query(models.Book.id).all()]
    else:
        # Merge explicit hash_ids with the books beneath any selected directories.
        # _expand_dirs_to_hash_ids now lives in routers/admin_books.py; main
        # re-exports it, and the call-time import avoids a load-time import cycle.
        import main
        hash_ids = list(dict.fromkeys((payload.hash_ids or []) + main._expand_dirs_to_hash_ids(db, payload.paths)))
        if not hash_ids:
            raise HTTPException(status_code=400, detail="hash_ids must be non-empty")

    running = _running_job_id()
    if running:
        raise HTTPException(
            status_code=409,
            detail={"reason": "job_running", "running_job_id": running},
        )

    job_id = uuid.uuid4().hex
    job = {
        "job_id": job_id,
        "status": "running",
        "mode": payload.mode,
        "total": len(hash_ids),
        "processed": 0,
        "ok_count": 0,
        "fail_count": 0,
        "started_at": _now_iso(),
        "finished_at": None,
        "cancel_requested": False,
        "error": None,
        "hash_ids": hash_ids,
        "failures": [],
        "all_results": [],
        "all_results_truncated": False,
    }
    with INTEGRITY_JOBS_LOCK:
        INTEGRITY_JOBS[job_id] = job

    asyncio.create_task(_run_integrity_job(job_id))
    return _job_summary(job)


@router.get("/api/admin/integrity/jobs")
async def admin_list_integrity_jobs(_admin: models.User = Depends(require_admin)):
    _sweep_expired_jobs()
    with INTEGRITY_JOBS_LOCK:
        jobs = [_job_summary(j) for j in INTEGRITY_JOBS.values()]
    jobs.sort(key=lambda j: j["started_at"], reverse=True)
    return {"jobs": jobs}


@router.get("/api/admin/integrity/jobs/{job_id}", response_model=schemas.IntegrityJobDetail)
async def admin_get_integrity_job(
    job_id: str,
    include: str = "failures",
    _admin: models.User = Depends(require_admin),
):
    with INTEGRITY_JOBS_LOCK:
        job = INTEGRITY_JOBS.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        detail = _job_summary(job)
        detail["failures"] = list(job["failures"])
        if include == "all":
            detail["all_results"] = list(job["all_results"])
        else:
            detail["all_results"] = None
        detail["all_results_truncated"] = bool(job.get("all_results_truncated"))
    return detail


@router.delete("/api/admin/integrity/jobs/{job_id}", response_model=schemas.IntegrityJobSummary)
async def admin_cancel_integrity_job(
    job_id: str,
    _admin: models.User = Depends(require_admin),
):
    with INTEGRITY_JOBS_LOCK:
        job = INTEGRITY_JOBS.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        if job["status"] == "running":
            job["cancel_requested"] = True
        return _job_summary(job)
