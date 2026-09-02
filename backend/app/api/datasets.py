"""
Dataset upload and selection endpoints.

Upload flow: validate the raw CSV bytes (app/data/validate_upload.py) ->
run the real pipeline against the validated DataFrame
(app/api/pipeline.run_uploaded_dataset) -> swap it in as the active
dataset (AppState.swap_dataset, called inside run_uploaded_dataset).

This endpoint deliberately does NOT catch and mask exceptions from the
pipeline itself (detection/retrieval/agent/policy/executor) -- those are
real bugs if they occur and should surface as 500s during a demo, not be
silently swallowed. What IS caught and turned into a clean 4xx is
anything from validate_upload_bytes, since malformed user input reaching
that point is an expected, everyday occurrence, not a bug.
"""

from __future__ import annotations

from fastapi import APIRouter, File, HTTPException, Request, UploadFile

from app.api.pipeline import run_uploaded_dataset, seed_from_synthetic_dataset
from app.api.state import AppState
from app.data.validate_upload import DatasetValidationError, validate_upload_bytes

router = APIRouter(prefix="/api/datasets", tags=["datasets"])


def _get_state(request: Request) -> AppState:
    return request.app.state.app_state


@router.get("")
def list_datasets(request: Request):
    """All datasets run so far this process (seeded + any uploads),
    plus which one is currently active -- lets the frontend render a
    selector without re-running anything.
    """
    state = _get_state(request)
    return {
        "active_dataset_id": state.active_dataset.dataset_id if state.active_dataset else None,
        "datasets": [
            {
                "dataset_id": info.dataset_id,
                "label": info.label,
                "kind": info.kind,
                "row_count": info.row_count,
                "candidate_count": info.candidate_count,
                "uploaded_at": info.uploaded_at,
                "original_filename": info.original_filename,
            }
            for info in sorted(
                state.dataset_history.values(),
                key=lambda d: (d.kind != "seeded", d.uploaded_at or ""),
            )
        ],
    }


@router.post("/upload")
async def upload_dataset(request: Request, file: UploadFile = File(...)):
    state = _get_state(request)

    if file.filename and not file.filename.lower().endswith(".csv"):
        raise HTTPException(
            status_code=400,
            detail={
                "errors": [
                    {
                        "code": "wrong_file_type",
                        "message": f"Expected a .csv file, got {file.filename!r}.",
                    }
                ]
            },
        )

    raw_bytes = await file.read()

    try:
        result = validate_upload_bytes(raw_bytes, filename=file.filename)
    except DatasetValidationError as e:
        raise HTTPException(status_code=422, detail=e.to_dict())

    info = run_uploaded_dataset(state, result.transactions_df, original_filename=file.filename)

    return {
        "dataset_id": info.dataset_id,
        "label": info.label,
        "row_count": info.row_count,
        "candidate_count": info.candidate_count,
        "validation_summary": result.to_summary_dict(),
    }


@router.post("/activate/{dataset_id}")
def activate_dataset(dataset_id: str, request: Request):
    """Switch the active dataset to one already run this process (the
    seeded dataset, or a previously uploaded one) WITHOUT re-uploading or
    re-running detection/agent/policy -- this only makes sense for
    "seeded", since re-running the pipeline for a past upload would need
    its original transactions, which swap_dataset intentionally doesn't
    retain (see AppState.swap_dataset's docstring). Re-selecting the
    currently active dataset is a harmless no-op.
    """
    state = _get_state(request)

    if state.active_dataset and state.active_dataset.dataset_id == dataset_id:
        return {"status": "already_active", "dataset_id": dataset_id}

    if dataset_id == "seeded":
        seed_from_synthetic_dataset(state)
        return {"status": "activated", "dataset_id": "seeded"}

    raise HTTPException(
        status_code=409,
        detail=(
            f"Cannot re-activate dataset {dataset_id!r}: only the seeded dataset can be "
            "re-run on demand. Re-upload the CSV to run an uploaded dataset again."
        ),
    )
