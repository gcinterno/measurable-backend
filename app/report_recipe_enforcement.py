from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from .report_recipe_validation import RecipeValidationResult, validate_blocks_against_recipe
from .report_recipes import ReportRecipe


def _block_value(block: Any, key: str) -> Any:
    if isinstance(block, dict):
        return block.get(key)
    return getattr(block, key, None)


def _block_data(block: Any) -> dict[str, Any]:
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
    if raw_order is None or isinstance(raw_order, bool):
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


def _recipe_structure(recipe: ReportRecipe) -> list[dict[str, Any]]:
    return [
        {
            "order": slide.order,
            "semantic_name": slide.semantic_name,
        }
        for slide in recipe.slides
    ]


def _block_structure(blocks: list[Any] | tuple[Any, ...]) -> list[dict[str, Any]]:
    return [
        {
            "index": index,
            "order": _block_order(block),
            "semantic_name": _block_semantic_name(block),
        }
        for index, block in enumerate(blocks, start=1)
    ]


class ReportRecipeEnforcementError(RuntimeError):
    def __init__(
        self,
        *,
        recipe: ReportRecipe,
        blocks: list[Any] | tuple[Any, ...],
        validation_result: RecipeValidationResult,
    ) -> None:
        self.recipe_id = recipe.id
        self.expected_structure = _recipe_structure(recipe)
        self.actual_structure = _block_structure(blocks)
        self.validation_result = validation_result
        self.validation_errors = tuple(asdict(error) for error in validation_result.errors)
        error_codes = ", ".join(error.code for error in validation_result.errors) or "UNKNOWN"
        super().__init__(
            f"Report blocks do not match recipe {recipe.id}: {error_codes}"
        )

    def to_log_payload(self) -> dict[str, Any]:
        return {
            "recipe_id": self.recipe_id,
            "expected_structure": self.expected_structure,
            "actual_structure": self.actual_structure,
            "validation_errors": list(self.validation_errors),
        }


def enforce_report_recipe(
    blocks: list[Any] | tuple[Any, ...],
    recipe: ReportRecipe,
) -> list[Any] | tuple[Any, ...]:
    validation_result = validate_blocks_against_recipe(blocks, recipe)
    if validation_result.valid:
        return blocks
    raise ReportRecipeEnforcementError(
        recipe=recipe,
        blocks=blocks,
        validation_result=validation_result,
    )


def should_enforce_facebook_pages_5_recipe(
    *,
    report_source: str,
    integration_type: str,
    effective_slide_limit: int,
) -> bool:
    return (
        report_source == "meta_pages_v2"
        and integration_type in {"facebook_pages", "meta_pages"}
        and effective_slide_limit == 5
    )


__all__ = [
    "ReportRecipeEnforcementError",
    "enforce_report_recipe",
    "should_enforce_facebook_pages_5_recipe",
]
