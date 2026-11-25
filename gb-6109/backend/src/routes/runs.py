from fastapi import APIRouter, Depends, HTTPException, Response

from src.config.resources import engine
from src.constants.job_state import JobState
from src.controllers.access import identity, reviewer
from src.controllers.workbench import new_review
from src.schemas.reviews import ReviewEntry, ReviewInput
from src.schemas.runs import RunEntry
from src.services import reviews, runs
from src.utils.serial import canonical_json

router = APIRouter(prefix="/runs", tags=["Execution"])


@router.get("", response_model=list[RunEntry], dependencies=[Depends(identity)])
def list_runs():
    with engine.connect() as db:
        return runs.list_entries(db)


@router.get("/{run_id}", response_model=RunEntry, dependencies=[Depends(identity)])
def run_detail(run_id: int):
    with engine.connect() as db:
        return runs.get(db, run_id)


@router.get("/{run_id}/root", dependencies=[Depends(identity)])
@router.get("/{run_id}/roots", dependencies=[Depends(identity)])
@router.get("/{run_id}/root-board", dependencies=[Depends(identity)], include_in_schema=False)
@router.get("/{run_id}/visible-roots", dependencies=[Depends(identity)], include_in_schema=False)
def run_roots(run_id: int):
    with engine.connect() as db:
        run = runs.get(db, run_id)
    result = run.get("result") or {}
    board = result.get("root_board") or result.get("roots")
    if board is None:
        return {"run_id": run_id, "available": False, "entries": [], "quarantine": []}
    return {"run_id": run_id, "available": True, **board}


@router.get("/{run_id}/quarantine", dependencies=[Depends(identity)])
@router.get("/{run_id}/isolation", dependencies=[Depends(identity)], include_in_schema=False)
def run_quarantine(run_id: int):
    roots = run_roots(run_id)
    return {"run_id": run_id, "quarantine": roots.get("quarantine", []), "counts": roots.get("counts", {})}


@router.get("/{run_id}/repair", dependencies=[Depends(identity)])
def run_repair_tickets(run_id: int):
    with engine.connect() as db:
        run = runs.get(db, run_id)
    result = run.get("result") or {}
    return {
        "run_id": run_id,
        "tickets": result.get("repair_tickets", []),
        "history": result.get("repair_history", []),
    }


@router.get("/{run_id}/review", response_model=ReviewEntry | None, dependencies=[Depends(identity)])
def review_detail(run_id: int):
    with engine.connect() as db:
        runs.get(db, run_id)
        return reviews.get(db, run_id)


@router.post("/{run_id}/review", response_model=ReviewEntry, status_code=201)
def sign(run_id: int, form: ReviewInput, user=Depends(reviewer)):
    return new_review(run_id, form, user)


@router.get("/{run_id}/export", dependencies=[Depends(identity)])
def export(run_id: int):
    with engine.connect() as db:
        run = runs.get(db, run_id)
        if run["status"] != JobState.DONE:
            raise HTTPException(409, "没有可导出的执行结果")
        run["review"] = reviews.get(db, run_id)
    return Response(canonical_json(run), media_type="application/json", headers={
        "Content-Disposition": f"attachment; filename=evidence-{run_id}.json",
    })
