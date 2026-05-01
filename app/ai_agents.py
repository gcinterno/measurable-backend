import json
import logging
from typing import Any

from .services import get_plan_capabilities, normalize_workspace_plan

logger = logging.getLogger(__name__)

AI_AGENT_VERSION = "v1-mock"
AI_AGENT_PROVIDER = "mock"
SUPPORTED_AI_MODES = {"standard", "agents"}


def normalize_ai_mode(value: Any) -> str:
    mode = str(value or "standard").strip().lower()
    return mode if mode in SUPPORTED_AI_MODES else "standard"


def build_ai_agent_plan_context(
    *,
    plan: str,
    effective_slide_limit: int,
    dataset_context: dict[str, Any] | None = None,
    report_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized_plan = normalize_workspace_plan(plan)
    capabilities = get_plan_capabilities(normalized_plan)
    return {
        "plan": normalized_plan,
        "max_slides": int(capabilities["max_slides"]),
        "allow_ai_agents": bool(capabilities["allow_ai_agents"]),
        "effective_slide_limit": min(
            int(effective_slide_limit),
            int(capabilities["max_slides"]),
        ),
        "capabilities": capabilities,
        "dataset_context": dataset_context or {},
        "report_context": report_context or {},
    }


def _parse_block_spec(block: dict[str, Any]) -> dict[str, Any] | None:
    block_type = str(block.get("type") or "").strip()
    if not block_type:
        return None

    data = block.get("data")
    if data is None:
        data = block.get("data_json")
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError:
            data = {}
    if not isinstance(data, dict):
        data = {}

    editable_fields = block.get("editable_fields")
    if editable_fields is None:
        editable_fields = block.get("editable_fields_json")
    if isinstance(editable_fields, str):
        try:
            editable_fields = json.loads(editable_fields)
        except json.JSONDecodeError:
            editable_fields = []
    if not isinstance(editable_fields, list):
        editable_fields = []

    return {
        "type": block_type,
        "order": int(block.get("order") or 0),
        "data": data,
        "editable_fields": editable_fields,
    }


def _to_block_spec(slide: dict[str, Any], order: int) -> dict[str, Any] | None:
    normalized = _parse_block_spec(slide)
    if normalized is None:
        return None
    return {
        "type": normalized["type"],
        "order": order,
        "data_json": json.dumps(normalized["data"]),
        "editable_fields_json": json.dumps(normalized["editable_fields"]),
    }


def _normalize_block_specs(
    block_specs: list[dict[str, Any]],
    *,
    effective_slide_limit: int,
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for index, block in enumerate(block_specs[:effective_slide_limit], start=1):
        block_spec = _to_block_spec(block, index)
        if block_spec is not None:
            normalized.append(block_spec)
    return normalized


def _make_block(
    block_type: str,
    order: int,
    data: dict[str, Any],
    editable_fields: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "type": block_type,
        "order": order,
        "data_json": json.dumps(data),
        "editable_fields_json": json.dumps(editable_fields or []),
    }


def _as_number(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _format_number(value: Any) -> str:
    numeric = _as_number(value)
    if numeric is None:
        return "N/A"
    if numeric.is_integer():
        return f"{int(numeric):,}"
    return f"{numeric:,.1f}"


def _valid_points(points: Any) -> list[dict[str, Any]]:
    if not isinstance(points, list):
        return []
    valid = []
    for point in points:
        if not isinstance(point, dict):
            continue
        value = _as_number(point.get("value"))
        if value is None:
            continue
        valid.append({"date": point.get("date"), "value": value})
    return valid


def _series_stats(points: Any) -> dict[str, Any]:
    valid = _valid_points(points)
    if not valid:
        return {
            "points_count": 0,
            "total": None,
            "average": None,
            "highest": None,
            "lowest": None,
            "first": None,
            "last": None,
            "delta": None,
        }
    total = sum(float(point["value"]) for point in valid)
    highest = max(valid, key=lambda point: float(point["value"]))
    lowest = min(valid, key=lambda point: float(point["value"]))
    first = valid[0]
    last = valid[-1]
    return {
        "points_count": len(valid),
        "total": total,
        "average": total / len(valid),
        "highest": highest,
        "lowest": lowest,
        "first": first,
        "last": last,
        "delta": float(last["value"]) - float(first["value"]),
    }


def _metric_value(report_inputs: dict[str, Any], key: str) -> Any:
    value = report_inputs.get(key)
    return value if value is not None else "N/A"


def _timeframe(dataset_context: dict[str, Any]) -> dict[str, Any]:
    timeframe = dataset_context.get("timeframe")
    return timeframe if isinstance(timeframe, dict) else {}


def _timeframe_label(dataset_context: dict[str, Any]) -> str:
    timeframe = _timeframe(dataset_context)
    return str(timeframe.get("label") or timeframe.get("key") or "the selected period")


def _report_inputs(dataset_context: dict[str, Any]) -> dict[str, Any]:
    report_inputs = dataset_context.get("report_inputs")
    return report_inputs if isinstance(report_inputs, dict) else {}


def _reach_chart(dataset_context: dict[str, Any]) -> dict[str, Any]:
    reach_chart = dataset_context.get("reach_chart_data")
    return reach_chart if isinstance(reach_chart, dict) else {}


def _impressions_payload(dataset_context: dict[str, Any]) -> dict[str, Any]:
    payload = dataset_context.get("impressions_slide_payload")
    return payload if isinstance(payload, dict) else {}


def _trend_text(metric_name: str, stats: dict[str, Any], timeframe_label: str) -> str:
    if not stats.get("points_count"):
        return f"{metric_name} daily data is not available for {timeframe_label}."
    highest = stats["highest"]
    lowest = stats["lowest"]
    delta = stats["delta"]
    direction = "increased" if delta and delta > 0 else "decreased" if delta and delta < 0 else "stayed flat"
    return (
        f"For {timeframe_label}, {metric_name.lower()} averaged "
        f"{_format_number(stats['average'])} per day. The highest day was "
        f"{highest.get('date') or 'N/A'} with {_format_number(highest.get('value'))}, "
        f"and the lowest day was {lowest.get('date') or 'N/A'} with "
        f"{_format_number(lowest.get('value'))}. The period {direction} from "
        f"{_format_number(stats['first'].get('value'))} to {_format_number(stats['last'].get('value'))}."
    )


def structure_agent(
    *,
    plan_context: dict[str, Any],
    block_specs: list[dict[str, Any]],
    dataset_context: dict[str, Any],
    report_context: dict[str, Any],
) -> dict[str, Any]:
    effective_slide_limit = int(plan_context["effective_slide_limit"])
    timeframe = _timeframe(dataset_context)
    timeframe_label = _timeframe_label(dataset_context)
    report_inputs = _report_inputs(dataset_context)
    reach_chart = _reach_chart(dataset_context)
    impressions_payload = _impressions_payload(dataset_context)
    title = str(report_context.get("title") or report_inputs.get("page_name") or "Marketing Report")
    page_name = str(report_inputs.get("page_name") or dataset_context.get("page_name") or "your page")
    slides = [
        _make_block(
            "title",
            1,
            {
                "text": title,
                "timeframe": timeframe,
                "period_label": timeframe.get("label"),
                "period_since": timeframe.get("since"),
                "period_until": timeframe.get("until"),
                "agent_slide": "cover",
            },
            ["text"],
        ),
        _make_block(
            "text",
            2,
            {
                "text": f"{page_name} performance summary for {timeframe_label}.",
                "agent_slide": "summary",
            },
            ["text"],
        ),
        _make_block(
            "text",
            3,
            {
                "text": "Key metrics overview.",
                "metrics": {
                    "followers": _metric_value(report_inputs, "followers"),
                    "reach": _metric_value(report_inputs, "reach"),
                    "engagement": _metric_value(report_inputs, "engagement"),
                },
                "agent_slide": "key_metrics",
            },
            ["text"],
        ),
        _make_block(
            "chart",
            4,
            {
                **reach_chart,
                "label": reach_chart.get("label") or f"Reach Trend - {timeframe_label}",
                "agent_slide": "reach_trend",
            },
        ),
        _make_block(
            "text",
            5,
            {
                "text": "Reach insight.",
                "timeframe": timeframe,
                "timeframe_label": timeframe_label,
                "agent_slide": "reach_insight",
            },
            ["text"],
        ),
        _make_block(
            "impressions_slide",
            6,
            {
                **impressions_payload,
                "label": impressions_payload.get("label") or f"Impressions - {timeframe_label}",
                "timeframe": impressions_payload.get("timeframe") or timeframe,
                "agent_slide": "impressions",
            },
        ),
        _make_block(
            "text",
            7,
            {
                "text": "Top post data is not available in this synced dataset.",
                "agent_slide": "top_post",
            },
            ["text"],
        ),
        _make_block(
            "text",
            8,
            {
                "text": "Recommendations will be generated from the current performance pattern.",
                "agent_slide": "recommendations",
            },
            ["text"],
        ),
    ]
    slides = _normalize_block_specs(slides, effective_slide_limit=effective_slide_limit)
    logger.info(
        "[AIAgents][mock.structure]",
        extra={
            "dataset_id": dataset_context.get("dataset_id"),
            "plan": plan_context.get("plan"),
            "ai_mode": report_context.get("ai_mode"),
            "effective_slide_limit": effective_slide_limit,
            "slides_count": len(slides),
            "slide_types": [str(slide.get("type")) for slide in slides],
        },
    )
    logger.info(
        "[AIAgents][structure.success]",
        extra={
            "dataset_id": dataset_context.get("dataset_id"),
            "plan": plan_context.get("plan"),
            "ai_mode": report_context.get("ai_mode"),
            "allow_ai_agents": plan_context.get("allow_ai_agents"),
            "effective_slide_limit": effective_slide_limit,
            "slides_count": len(slides),
        },
    )
    return {
        "slides": slides,
        "summary": {
            "slides_count": len(slides),
            "slide_types": [str(slide.get("type")) for slide in slides],
        },
    }


def insight_agent(
    *,
    plan_context: dict[str, Any],
    structure: dict[str, Any],
    dataset_context: dict[str, Any],
    report_context: dict[str, Any],
) -> dict[str, Any]:
    slides = list(structure.get("slides") or [])
    timeframe_label = _timeframe_label(dataset_context)
    report_inputs = _report_inputs(dataset_context)
    reach_points = _reach_chart(dataset_context).get("points")
    impressions_points = _impressions_payload(dataset_context).get("impressions_daily")
    reach_stats = _series_stats(reach_points)
    impressions_stats = _series_stats(impressions_points)
    page_name = str(report_inputs.get("page_name") or dataset_context.get("page_name") or "This page")
    for slide in slides:
        normalized = _parse_block_spec(slide)
        if normalized is None:
            continue
        data = normalized["data"]
        agent_slide = data.get("agent_slide")
        if agent_slide == "summary":
            data["text"] = (
                f"{page_name} reached {_format_number(report_inputs.get('reach'))} people and generated "
                f"{_format_number(report_inputs.get('engagement'))} engagements during {timeframe_label}."
            )
        elif agent_slide == "key_metrics":
            data["text"] = (
                f"Key metrics for {timeframe_label}: "
                f"{_format_number(report_inputs.get('followers'))} followers, "
                f"{_format_number(report_inputs.get('reach'))} reach, and "
                f"{_format_number(report_inputs.get('engagement'))} engagements."
            )
        elif agent_slide == "reach_insight":
            data["text"] = _trend_text("Reach", reach_stats, timeframe_label)
        elif agent_slide == "impressions":
            data["insight_text"] = _trend_text("Impressions", impressions_stats, timeframe_label)
            data["impressions_daily_count"] = impressions_stats.get("points_count")
        elif agent_slide == "recommendations":
            reach_delta = reach_stats.get("delta")
            direction = "positive" if reach_delta and reach_delta > 0 else "softening"
            data["text"] = (
                f"Recommended next step: prioritize content formats that drove the strongest days in "
                f"{timeframe_label}. The reach trend is {direction}, so use the best-performing days "
                "as references for posting cadence and creative direction."
            )
        slide["data_json"] = json.dumps(data)
    logger.info(
        "[AIAgents][mock.insight]",
        extra={
            "dataset_id": dataset_context.get("dataset_id"),
            "plan": plan_context.get("plan"),
            "ai_mode": report_context.get("ai_mode"),
            "timeframe_label": timeframe_label,
            "reach_points": reach_stats.get("points_count"),
            "impressions_points": impressions_stats.get("points_count"),
            "slides_count": len(slides),
        },
    )
    logger.info(
        "[AIAgents][insight.success]",
        extra={
            "dataset_id": dataset_context.get("dataset_id"),
            "plan": plan_context.get("plan"),
            "ai_mode": report_context.get("ai_mode"),
            "allow_ai_agents": plan_context.get("allow_ai_agents"),
            "effective_slide_limit": plan_context.get("effective_slide_limit"),
            "slides_count": len(slides),
        },
    )
    return {"slides": slides}


def design_agent(
    *,
    plan_context: dict[str, Any],
    structure: dict[str, Any],
    insights: dict[str, Any],
    dataset_context: dict[str, Any],
    report_context: dict[str, Any],
) -> dict[str, Any]:
    slides = list(insights.get("slides") or structure.get("slides") or [])
    for slide in slides:
        normalized = _parse_block_spec(slide)
        if normalized is None:
            continue
        data = normalized["data"]
        agent_slide = str(data.get("agent_slide") or "")
        if agent_slide in {"cover", "summary"}:
            layout_hint = "executive"
        elif agent_slide in {"reach_trend", "impressions"}:
            layout_hint = "data_first"
        else:
            layout_hint = "balanced"
        data["design_hints"] = {
            "theme": "current_template",
            "emphasis": layout_hint,
            "layout_hint": agent_slide or normalized["type"],
        }
        slide["data_json"] = json.dumps(data)
    logger.info(
        "[AIAgents][mock.design]",
        extra={
            "dataset_id": dataset_context.get("dataset_id"),
            "plan": plan_context.get("plan"),
            "ai_mode": report_context.get("ai_mode"),
            "slides_count": len(slides),
            "theme": "current_template",
        },
    )
    logger.info(
        "[AIAgents][design.success]",
        extra={
            "dataset_id": dataset_context.get("dataset_id"),
            "plan": plan_context.get("plan"),
            "ai_mode": report_context.get("ai_mode"),
            "allow_ai_agents": plan_context.get("allow_ai_agents"),
            "effective_slide_limit": plan_context.get("effective_slide_limit"),
            "slides_count": len(slides),
        },
    )
    return {
        "slides": slides,
        "design_summary": {
            "layout_strategy": "preserve_current_renderer_contract",
            "render_contract": "ReportBlock",
        },
    }


def build_ai_agent_metadata(
    *,
    ai_mode: str,
    allow_ai_agents: bool,
    pipeline_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result = pipeline_result or {}
    return {
        "ai_mode": ai_mode,
        "ai_agents_enabled": bool(allow_ai_agents),
        "ai_agents_used": bool(result.get("used", False)),
        "ai_provider": AI_AGENT_PROVIDER,
        "ai_agent_version": AI_AGENT_VERSION,
        "ai_agent_fallback_used": bool(result.get("fallback_used", False)),
        "ai_agent_errors": list(result.get("errors") or []),
        "ai_structure_summary": result.get("structure_summary"),
    }


def run_ai_agents_pipeline(
    *,
    ai_mode: str,
    plan_context: dict[str, Any],
    block_specs: list[dict[str, Any]],
    dataset_context: dict[str, Any],
    report_context: dict[str, Any],
) -> dict[str, Any]:
    effective_slide_limit = int(plan_context["effective_slide_limit"])
    normalized_mode = normalize_ai_mode(ai_mode)
    logger.info(
        "[AIAgents][pipeline.start]",
        extra={
            "dataset_id": dataset_context.get("dataset_id"),
            "plan": plan_context.get("plan"),
            "ai_mode": normalized_mode,
            "allow_ai_agents": plan_context.get("allow_ai_agents"),
            "effective_slide_limit": effective_slide_limit,
        },
    )

    if normalized_mode != "agents" or not plan_context.get("allow_ai_agents"):
        final_blocks = _normalize_block_specs(block_specs, effective_slide_limit=effective_slide_limit)
        logger.info(
            "[AIAgents][pipeline.final]",
            extra={
                "dataset_id": dataset_context.get("dataset_id"),
                "plan": plan_context.get("plan"),
                "ai_mode": normalized_mode,
                "allow_ai_agents": plan_context.get("allow_ai_agents"),
                "effective_slide_limit": effective_slide_limit,
                "fallback_used": False,
                "number_of_blocks_final": len(final_blocks),
            },
        )
        return {
            "blocks": final_blocks,
            "used": False,
            "fallback_used": False,
            "errors": [],
            "structure_summary": None,
        }

    try:
        structure = structure_agent(
            plan_context=plan_context,
            block_specs=block_specs,
            dataset_context=dataset_context,
            report_context=report_context,
        )
        insights = insight_agent(
            plan_context=plan_context,
            structure=structure,
            dataset_context=dataset_context,
            report_context=report_context,
        )
        design = design_agent(
            plan_context=plan_context,
            structure=structure,
            insights=insights,
            dataset_context=dataset_context,
            report_context=report_context,
        )
        final_blocks = _normalize_block_specs(
            list(design.get("slides") or []),
            effective_slide_limit=effective_slide_limit,
        )
        if not final_blocks:
            raise ValueError("AI agents pipeline returned no valid blocks.")
        logger.info(
            "[AIAgents][pipeline.final]",
            extra={
                "dataset_id": dataset_context.get("dataset_id"),
                "plan": plan_context.get("plan"),
                "ai_mode": normalized_mode,
                "allow_ai_agents": plan_context.get("allow_ai_agents"),
                "effective_slide_limit": effective_slide_limit,
                "fallback_used": False,
                "number_of_blocks_final": len(final_blocks),
            },
        )
        return {
            "blocks": final_blocks,
            "used": True,
            "fallback_used": False,
            "errors": [],
            "structure_summary": structure.get("summary"),
        }
    except Exception as exc:
        logger.exception(
            "[AIAgents][pipeline.error]",
            extra={
                "dataset_id": dataset_context.get("dataset_id"),
                "plan": plan_context.get("plan"),
                "ai_mode": normalized_mode,
                "allow_ai_agents": plan_context.get("allow_ai_agents"),
                "effective_slide_limit": effective_slide_limit,
                "error": str(exc),
            },
        )
        fallback_blocks = _normalize_block_specs(block_specs, effective_slide_limit=effective_slide_limit)
        logger.warning(
            "[AIAgents][pipeline.fallback]",
            extra={
                "dataset_id": dataset_context.get("dataset_id"),
                "plan": plan_context.get("plan"),
                "ai_mode": normalized_mode,
                "allow_ai_agents": plan_context.get("allow_ai_agents"),
                "effective_slide_limit": effective_slide_limit,
                "fallback_used": True,
                "number_of_blocks_final": len(fallback_blocks),
            },
        )
        logger.info(
            "[AIAgents][pipeline.final]",
            extra={
                "dataset_id": dataset_context.get("dataset_id"),
                "plan": plan_context.get("plan"),
                "ai_mode": normalized_mode,
                "allow_ai_agents": plan_context.get("allow_ai_agents"),
                "effective_slide_limit": effective_slide_limit,
                "fallback_used": True,
                "number_of_blocks_final": len(fallback_blocks),
            },
        )
        return {
            "blocks": fallback_blocks,
            "used": False,
            "fallback_used": True,
            "errors": [str(exc)],
            "structure_summary": None,
        }
