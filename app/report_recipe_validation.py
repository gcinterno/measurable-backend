from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .report_recipes import ReportRecipe


@dataclass(frozen=True)
class RecipeValidationError:
    code: str
    message: str
    block_index: int | None = None
    expected_order: int | None = None
    actual_order: int | None = None
    expected_semantic_name: str | None = None
    actual_semantic_name: str | None = None


@dataclass(frozen=True)
class RecipeValidationResult:
    valid: bool
    expected_count: int
    actual_count: int
    errors: tuple[RecipeValidationError, ...]


def _block_value(block: Any, key: str) -> Any:
    if isinstance(block, dict):
        return block.get(key)
    return getattr(block, key, None)


def _block_data(block: Any) -> dict[str, Any]:
    direct_data = _block_value(block, "data")
    if isinstance(direct_data, dict):
        return direct_data

    raw_data = _block_value(block, "data_json")
    if isinstance(raw_data, dict):
        return raw_data
    if isinstance(raw_data, str):
        try:
            parsed = json.loads(raw_data)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _block_order(block: Any) -> int | None:
    raw_order = _block_value(block, "order")
    if isinstance(raw_order, bool) or raw_order is None:
        return None
    try:
        return int(raw_order)
    except (TypeError, ValueError):
        return None


def _block_semantic_name(block: Any) -> str | None:
    direct = _block_value(block, "semantic_name") or _block_value(block, "semanticName")
    if direct is not None:
        semantic_name = str(direct).strip()
        return semantic_name or None

    data = _block_data(block)
    raw = data.get("semantic_name") or data.get("semanticName")
    if raw is None:
        return None
    semantic_name = str(raw).strip()
    return semantic_name or None


def validate_blocks_against_recipe(
    blocks: list[Any] | tuple[Any, ...],
    recipe: ReportRecipe,
) -> RecipeValidationResult:
    """Compare block structure with a Recipe without mutating either input.

    V1 validates only slide count, persisted block order, and semantic name.
    """

    actual_blocks = tuple(blocks)
    expected_slides = tuple(recipe.slides)
    errors: list[RecipeValidationError] = []
    blocks_by_order: dict[int, tuple[int, str | None]] = {}
    expected_orders = {slide.order for slide in expected_slides}

    if len(actual_blocks) != len(expected_slides):
        errors.append(
            RecipeValidationError(
                code="SLIDE_COUNT_MISMATCH",
                message=(
                    f"Expected {len(expected_slides)} slides for recipe {recipe.id}, "
                    f"got {len(actual_blocks)}."
                ),
            )
        )

    for index, block in enumerate(actual_blocks, start=1):
        actual_order = _block_order(block)
        actual_semantic_name = _block_semantic_name(block)

        if actual_order is None:
            errors.append(
                RecipeValidationError(
                    code="MISSING_ORDER",
                    message=f"Block at index {index} is missing order.",
                    block_index=index,
                )
            )
        elif actual_order in blocks_by_order:
            errors.append(
                RecipeValidationError(
                    code="ORDER_MISMATCH",
                    message=f"Duplicate block order {actual_order} at index {index}.",
                    block_index=index,
                    actual_order=actual_order,
                )
            )
        else:
            blocks_by_order[actual_order] = (index, actual_semantic_name)
            if actual_order not in expected_orders:
                errors.append(
                    RecipeValidationError(
                        code="ORDER_MISMATCH",
                        message=f"Unexpected block order {actual_order} at index {index}.",
                        block_index=index,
                        actual_order=actual_order,
                    )
                )

        if actual_semantic_name is None:
            errors.append(
                RecipeValidationError(
                    code="MISSING_SEMANTIC_NAME",
                    message=f"Block at index {index} is missing semantic_name.",
                    block_index=index,
                    actual_order=actual_order,
                )
            )

    for expected_slide in expected_slides:
        block_info = blocks_by_order.get(expected_slide.order)
        if block_info is None:
            errors.append(
                RecipeValidationError(
                    code="ORDER_MISMATCH",
                    message=f"Expected block order {expected_slide.order} is missing.",
                    expected_order=expected_slide.order,
                    expected_semantic_name=expected_slide.semantic_name,
                )
            )
            continue

        index, actual_semantic_name = block_info
        if actual_semantic_name is not None and actual_semantic_name != expected_slide.semantic_name:
            errors.append(
                RecipeValidationError(
                    code="SEMANTIC_NAME_MISMATCH",
                    message=(
                        f"Block order {expected_slide.order} expected semantic_name "
                        f"{expected_slide.semantic_name!r}, got {actual_semantic_name!r}."
                    ),
                    block_index=index,
                    expected_order=expected_slide.order,
                    actual_order=expected_slide.order,
                    expected_semantic_name=expected_slide.semantic_name,
                    actual_semantic_name=actual_semantic_name,
                )
            )

    return RecipeValidationResult(
        valid=not errors,
        expected_count=len(expected_slides),
        actual_count=len(actual_blocks),
        errors=tuple(errors),
    )


__all__ = [
    "RecipeValidationError",
    "RecipeValidationResult",
    "validate_blocks_against_recipe",
]
