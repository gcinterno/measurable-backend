from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from .deps import require_admin_user
from .models import User
from .report_spec import report_spec_capabilities


router = APIRouter(prefix="/report-spec", tags=["report-spec"])


@router.get("/capabilities")
def get_report_spec_capabilities(
    current_user: User = Depends(require_admin_user),
) -> dict[str, Any]:
    return report_spec_capabilities()


__all__ = ["get_report_spec_capabilities", "router"]
