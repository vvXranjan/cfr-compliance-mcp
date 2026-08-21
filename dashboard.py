"""dashboard.py

Thin server-rendered Jinja2 dashboard over the existing FastAPI app and
the persistence/review layer. It is a read-mostly human-review UI, NOT a
separate frontend service (no React/Vite/Node, no WebSockets).

The dashboard renders only data already exposed by the
`PersistenceRepository` and preserves the compliance authority model:

  * Authoritative CFR evidence is presented as authoritative.
  * Compliance Memory participation is shown as a clearly labeled,
    *advisory* "historical memory" badge -- never as regulation.
  * NEEDS_REVIEW is prominent; the human approval boundary is explicit.
  * No secrets, prompts, hidden reasoning, or API keys are ever rendered.

It reads through the SAME configured backend as the API (injected via
``set_repository``). With the default ``file`` backend the history pages
work; review pages show an informative notice that the review workflow
requires the PostgreSQL backend.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from agent.persistence.base import (
    AnalysisNotFoundError,
    ConcurrencyError,
    ReviewNotFoundError,
    ReviewNotSupportedError,
    ReviewStateError,
)
from agent.persistence.models import ReviewState
from agent.persistence.review_lifecycle import ALLOWED_TRANSITIONS

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


def _pct(part: int, total: int) -> int:
    """Percentage of ``part`` over ``total`` (0 when total is 0)."""
    if total <= 0:
        return 0
    return round(part / total * 100)


templates.env.globals["pct"] = _pct
templates.env.filters["pct"] = _pct

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

# The shared persistence repository, injected by api.py at include time.
_repo: Any = None
_reports_dir: str | None = os.getenv("CFR_REPORTS_DIR")


def set_repository(repo: Any, reports_dir: str | None = None) -> None:
    """Bind the dashboard to the application's configured repository."""
    global _repo, _reports_dir
    _repo = repo
    if reports_dir is not None:
        _reports_dir = reports_dir


def _repository() -> Any:
    if _repo is None:
        raise RuntimeError("Dashboard repository is not configured")
    return _repo


def _backend() -> str:
    return getattr(_repo, "backend", "file")


def _backend_ui() -> tuple[str, bool]:
    """Return a friendly backend label and an "online" flag.

    Exposes no DSNs or raw configuration details. PostgreSQL is shown as
    connected; the default file storage is shown neutrally.
    """
    if _backend() == "postgres":
        return "PostgreSQL Connected", True
    return "File Storage", False


def _base_ctx(active: str) -> dict[str, Any]:
    label, online = _backend_ui()
    return {
        "active": active,
        "backend": _backend(),
        "backend_label": label,
        "backend_online": online,
        "notice": None,
        "error": None,
    }


def _paginate(total: int, limit: int, offset: int) -> dict[str, Any]:
    """Compute pagination metadata for the templates."""
    page = (offset // limit) + 1 if limit else 1
    pages = max((total + limit - 1) // limit, 1) if limit else 1
    return {
        "page": page,
        "pages": pages,
        "has_prev": offset > 0,
        "has_next": offset + limit < total,
        "prev_offset": max(offset - limit, 0),
        "next_offset": offset + limit,
    }


# ---------------------------------------------------------------------------
# Overview
# ---------------------------------------------------------------------------


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def dashboard_index(request: Request) -> HTMLResponse:
    repo = _repository()
    metrics = repo.summarize(reports_dir=_reports_dir)
    recent = repo.list_analyses(limit=8, offset=0, reports_dir=_reports_dir)
    ctx = _base_ctx("overview")
    ctx.update({"request": request, "metrics": metrics, "recent": recent})
    return templates.TemplateResponse(request, "dashboard/index.html", ctx)


# ---------------------------------------------------------------------------
# Analysis history
# ---------------------------------------------------------------------------


@router.get("/analyses", response_class=HTMLResponse)
async def analyses_list(
    request: Request,
    status: Annotated[
        str | None,
        Query(pattern="^(compliant|non_compliant|needs_review)$"),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> HTMLResponse:
    repo = _repository()
    items = repo.list_analyses(
        status=status, limit=limit, offset=offset, reports_dir=_reports_dir
    )
    total = repo.count_analyses(status=status, reports_dir=_reports_dir)
    ctx = _base_ctx("analyses")
    ctx.update(
        {
            "request": request,
            "items": items,
            "total": total,
            "status": status,
            "limit": limit,
            "offset": offset,
            **(_paginate(total, limit, offset)),
        }
    )
    return templates.TemplateResponse(request, "dashboard/analyses_list.html", ctx)


@router.get("/analyses/{analysis_id}", response_class=HTMLResponse)
async def analysis_detail(request: Request, analysis_id: str) -> HTMLResponse:
    repo = _repository()
    try:
        record = repo.get_analysis(analysis_id, reports_dir=_reports_dir)
    except (AnalysisNotFoundError, FileNotFoundError):
        raise HTTPException(status_code=404, detail="Analysis not found") from None
    ctx = _base_ctx("analyses")
    ctx.update({"request": request, "record": record})
    return templates.TemplateResponse(request, "dashboard/analysis_detail.html", ctx)


# ---------------------------------------------------------------------------
# Human review
# ---------------------------------------------------------------------------


@router.get("/reviews", response_class=HTMLResponse)
async def reviews_list(
    request: Request,
    state: Annotated[
        ReviewState | None,
        Query(description="Filter by review state; defaults to needs_review."),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> HTMLResponse:
    repo = _repository()
    state_val = (state.value if state else None) or "needs_review"
    ctx = _base_ctx("reviews")
    ctx.update(
        {
            "request": request,
            "items": [],
            "total": 0,
            "state": state_val,
            "unsupported": False,
        }
    )
    try:
        items = repo.list_review_queue(state=state_val, limit=limit, offset=offset)
        ctx["total"] = repo.count_review_queue(state=state_val)
        ctx["items"] = items
        ctx.update(_paginate(ctx["total"], limit, offset))
    except ReviewNotSupportedError:
        ctx["unsupported"] = True
    return templates.TemplateResponse(request, "dashboard/reviews_list.html", ctx)


@router.get("/reviews/{analysis_id}/{clause_id}", response_class=HTMLResponse)
async def review_detail(
    request: Request,
    analysis_id: str,
    clause_id: str,
    saved: Annotated[int | None, Query()] = None,
    error: Annotated[str | None, Query()] = None,
) -> HTMLResponse:
    repo = _repository()
    ctx = _base_ctx("reviews")
    ctx.update(
        {
            "request": request,
            "notice": "Decision recorded." if saved else None,
            "error": _error_message(error),
            "unsupported": False,
            "detail": None,
            "allowed_targets": [],
        }
    )
    try:
        ctx["detail"] = repo.get_review(analysis_id, clause_id)
        ctx["allowed_targets"] = _allowed_targets(ctx["detail"])
    except ReviewNotSupportedError:
        ctx["unsupported"] = True
        return templates.TemplateResponse(request, "dashboard/review_detail.html", ctx)
    except (AnalysisNotFoundError, ReviewNotFoundError):
        raise HTTPException(status_code=404, detail="Review not found") from None
    return templates.TemplateResponse(request, "dashboard/review_detail.html", ctx)


@router.post("/reviews/{analysis_id}/{clause_id}/decide")
async def review_decide(
    analysis_id: str,
    clause_id: str,
    target_state: Annotated[ReviewState, Form()],
    reason: Annotated[str, Form()] = "",
    reviewer_identity: Annotated[str, Form()] = "",
    expected_version: Annotated[int, Form()] = 0,
) -> RedirectResponse:
    repo = _repository()
    try:
        repo.transition_review(
            analysis_id,
            clause_id,
            target_state=target_state.value,
            reason=reason,
            reviewer_identity=reviewer_identity,
            expected_version=expected_version,
        )
    except ReviewNotSupportedError:
        raise HTTPException(
            status_code=501,
            detail="The review workflow requires the PostgreSQL persistence backend",
        ) from None
    except ReviewStateError:
        return RedirectResponse(
            url=f"/dashboard/reviews/{analysis_id}/{clause_id}?error=invalid_transition",
            status_code=303,
        )
    except ConcurrencyError:
        return RedirectResponse(
            url=f"/dashboard/reviews/{analysis_id}/{clause_id}?error=concurrency_conflict",
            status_code=303,
        )
    except (AnalysisNotFoundError, ReviewNotFoundError):
        raise HTTPException(status_code=404, detail="Review not found") from None

    return RedirectResponse(
        url=f"/dashboard/reviews/{analysis_id}/{clause_id}?saved=1", status_code=303
    )


def _error_message(code: str | None) -> str | None:
    """Map a redirect error code to a safe, human-readable message."""
    return {
        "invalid_transition": (
            "Invalid review transition for the current state; a review must "
            "first be claimed (under review) before approving/rejecting."
        ),
        "concurrency_conflict": (
            "Version conflict: the record changed since it was loaded. "
            "Refresh the page and retry."
        ),
    }.get(code or "")


def _allowed_targets(detail: Any) -> list[str]:
    """Return the valid next review states for the current state."""
    if detail is None or not detail.review_state:
        return []
    return sorted(ALLOWED_TRANSITIONS.get(detail.review_state, set()))
