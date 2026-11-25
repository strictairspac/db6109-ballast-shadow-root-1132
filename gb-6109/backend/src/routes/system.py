from fastapi import APIRouter, Depends, Query
from sqlalchemy import desc, select, text

from src.config.resources import cache, engine
from src.constants.job_state import JobState
from src.controllers.access import identity
from src.domain.scenario import CONFIG
from src.models.runs import runs as runs_table
from src.services.audit import history
from src.services.runs import get as get_run

router = APIRouter(tags=["System"])


@router.get("/health")
def health():
    with engine.connect() as db:
        db.execute(text("SELECT 1"))
    cache.ping()
    return {"status": "healthy", "engine": engine.dialect.name}


@router.get("/workbench")
def workbench():
    return {**CONFIG, "engine": "PostgreSQL 16" if engine.dialect.name == "postgresql" else "MySQL 8.0"}


@router.get("/roots", dependencies=[Depends(identity)])
def visible_roots(run_id: int | None = None, recipe: str | None = None):
    """Query the visible recipe-root board, defaulting to the latest rehearsal."""
    with engine.connect() as db:
        if run_id is not None:
            run = get_run(db, run_id)
        else:
            row = db.execute(
                select(runs_table.c.id)
                .where(runs_table.c.status == JobState.DONE)
                .order_by(desc(runs_table.c.id))
                .limit(1)
            ).first()
            if row is None:
                return {"available": False, "entries": [], "quarantine": [], "counts": {}}
            run = get_run(db, row[0])
    result = run.get("result") or {}
    board = result.get("root_board") or result.get("roots")
    if board is None:
        return {"run_id": run["id"], "available": False, "entries": [], "quarantine": [], "counts": {}}
    if recipe:
        keyword = recipe.strip().lower()
        entries = [entry for entry in board.get("entries", [])
                   if keyword in str(entry.get("recipe", "")).lower()]
        return {
            "run_id": run["id"], "available": True, "recipe": recipe,
            "entries": entries,
            "quarantine": [entry for entry in entries if entry.get("quarantined")],
            "recovered": [entry for entry in entries if entry.get("recovered")],
            "counts": {
                "total": len(entries),
                "quarantined": sum(1 for entry in entries if entry.get("quarantined")),
                "recovered": sum(1 for entry in entries if entry.get("recovered")),
            },
        }
    return {"run_id": run["id"], "available": True, **board}


@router.get("/audit", dependencies=[Depends(identity)])
def recent_events(limit: int = Query(default=25, ge=1, le=100)):
    with engine.connect() as db:
        return history(db, limit)
