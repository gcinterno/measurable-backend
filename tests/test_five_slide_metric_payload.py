from __future__ import annotations

import json
import os

os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/measurable_five_slide_test.db?check_same_thread=false")
os.environ.setdefault("JWT_SECRET", "test-jwt-secret")

from app.main import build_5_blocks, extractDailyMetricSeries, truncateInsightForSlide


OLD_INSIGHT_PLACEHOLDERS = (
    "insights will appear",
    "daily resumen final",
    "source includes enough contextual detail",
    "Daily resumen final is available",
)


def _base_context(*, integration_type: str) -> dict:
    return {
        "title": "Executive report",
        "plan": "core",
        "report_timeframe": {"label": "Last 28 days", "since": "2026-05-01", "until": "2026-05-28"},
        "page_name": "Acme Account",
        "followers": 1200,
        "reach": 44851,
        "engagement": 320,
        "page_views": 5748,
        "summary": "Summary",
        "reach_chart_data": {
            "metric": "reach",
            "points": [
                {"date": "2026-05-15", "value": 1234},
                {"date": "2026-05-16", "value": 900},
            ],
            "timeframe": {"label": "Last 28 days"},
        },
        "reach_insight": "Reach insight",
        "recent_posts_summary": "Posts summary",
        "ai_summary": "AI summary",
        "general_insights_slide_payload": {},
        "report_inputs": {
            "integration_type": integration_type,
            "reach_daily": [
                {"date": "2026-05-15", "value": 1234},
                {"date": "2026-05-16", "value": 900},
            ],
            "engagement_daily": [
                {"date": "2026-05-15", "value": 80},
                {"date": "2026-05-16", "value": 40},
            ],
            "page_views_daily": [
                {"date": "2026-05-15", "value": 3300},
                {"date": "2026-05-16", "value": 2448},
            ],
            "engagement": 320,
            "page_views": 5748,
            "reach": 44851,
            "followers": 1200,
        },
        "branding": {},
        "requested_slides": 5,
    }


def test_extract_daily_metric_series_normalizes_nested_sources_and_zero_values():
    dataset = {
        "report_inputs": {
            "integration_type": "facebook_pages",
            "daily_metrics": {
                "page_impressions_unique": [
                    {"date": "2026-05-15", "value": "0"},
                    {"date": "2026-05-16", "value": "12"},
                ]
            },
            "values": {
                "total_interactions": [
                    {"date": "2026-05-15", "value": "4"},
                    {"date": "2026-05-16", "value": "0"},
                ]
            },
            "normalized_report_metrics": {
                "views_daily": [
                    {"date": "2026-05-15", "value": "8"},
                    {"date": "2026-05-16", "value": 0},
                ]
            },
        }
    }
    reach = extractDailyMetricSeries(dataset, "reach")
    engagement = extractDailyMetricSeries(dataset, "engagement")
    page_views = extractDailyMetricSeries(dataset, "page_views")
    assert reach == [
        {"date": "2026-05-15", "label": "May 15", "value": 0.0},
        {"date": "2026-05-16", "label": "May 16", "value": 12.0},
    ]
    assert engagement == [
        {"date": "2026-05-15", "label": "May 15", "value": 4.0},
        {"date": "2026-05-16", "label": "May 16", "value": 0.0},
    ]
    assert page_views == [
        {"date": "2026-05-15", "label": "May 15", "value": 8.0},
        {"date": "2026-05-16", "label": "May 16", "value": 0.0},
    ]


def test_extract_daily_metric_series_reads_normalized_report_metrics_and_reach_aliases():
    dataset = {
        "report_inputs": {
            "integration_type": "facebook_pages",
            "normalized_report_metrics": {
                "viewers_daily": [
                    {"date": "2026-05-15", "value": 1200},
                    {"date": "2026-05-16", "value": 900},
                ],
                "interactions_daily": [
                    {"date": "2026-05-15", "value": 600},
                    {"date": "2026-05-16", "value": 767},
                ],
                "page_visits_daily": [
                    {"date": "2026-05-15", "value": 71},
                    {"date": "2026-05-16", "value": 55},
                ],
            },
        }
    }
    assert extractDailyMetricSeries(dataset, "reach") == [
        {"date": "2026-05-15", "label": "May 15", "value": 1200},
        {"date": "2026-05-16", "label": "May 16", "value": 900},
    ]
    assert extractDailyMetricSeries(dataset, "engagement") == [
        {"date": "2026-05-15", "label": "May 15", "value": 600},
        {"date": "2026-05-16", "label": "May 16", "value": 767},
    ]
    assert extractDailyMetricSeries(dataset, "page_views") == [
        {"date": "2026-05-15", "label": "May 15", "value": 71},
        {"date": "2026-05-16", "label": "May 16", "value": 55},
    ]


def test_truncate_insight_for_slide_limits_to_280_chars():
    long_text = " ".join(["This is a long insight sentence."] * 20)
    short_text, full_text = truncateInsightForSlide(long_text, limit=280)
    assert len(short_text) <= 280
    assert len(full_text) > 280


def test_build_5_blocks_generates_new_metric_structure_for_facebook_pages():
    blocks = build_5_blocks(_base_context(integration_type="facebook_pages"))
    assert len(blocks) == 5
    cover = json.loads(blocks[0]["data_json"])
    reach = json.loads(blocks[1]["data_json"])
    impressions = json.loads(blocks[2]["data_json"])
    engagement = json.loads(blocks[3]["data_json"])
    summary = json.loads(blocks[4]["data_json"])

    assert cover["slide_number"] == 1
    assert cover["slide_type"] == "cover"
    assert cover["text"] == "Facebook Pages Report - Summary & Insights"
    assert cover["page_name"] == "Acme Account"
    assert cover["platform"] == "Facebook Pages"

    assert reach["slide_number"] == 2
    assert reach["slide_type"] == "metric"
    assert reach["metric_key"] == "reach"
    assert reach["metric_label"] == "Reach"
    assert reach["metric_label_es"] == "Alcance"
    assert reach["label"] == "Total Reach"
    assert reach["formatted_total"] == "44,851"
    assert reach["is_available"] is True
    assert reach["daily_series"][0]["date"] == "2026-05-15"
    assert reach["highest_day"]["value"] == 1234
    assert reach["lowest_day"]["value"] == 900

    assert impressions["slide_number"] == 3
    assert impressions["metric_key"] == "impressions"
    assert impressions["metric_label"] == "Impressions"
    assert impressions["metric_label_es"] == "Impresiones"
    assert impressions["label"] == "Total Impressions"
    assert impressions["formatted_total"] == "N/A"

    assert engagement["slide_number"] == 4
    assert engagement["metric_key"] == "engagement"
    assert engagement["metric_source"] == "direct_meta_metric"
    assert engagement["label"] == "Total Engagement"
    assert engagement["daily_series"][0]["value"] == 80
    assert engagement["highest_day"]["value"] == 80

    assert summary["slide_number"] == 5
    assert summary["slide_type"] == "summary"
    assert set(summary["metrics_summary"].keys()) == {"reach", "impressions", "engagement", "followers", "page_views"}
    assert summary["metrics_summary"]["reach"]["value"] == 44851
    assert summary["metrics_summary"]["impressions"]["value"] is None
    assert summary["metrics_summary"]["engagement"]["value"] == 320
    assert summary["metrics_summary"]["followers"]["value"] == 1200
    assert summary["metrics_summary"]["page_views"]["value"] == 5748

    for metric_slide in (reach, impressions, engagement):
        assert metric_slide["insight_tone"] == "executive_ai"
        assert metric_slide["insight_max_chars"] == 260
        assert len(metric_slide["insight_short"]) <= 260
        assert len(metric_slide["insight"]) <= 420
        assert not any(placeholder.lower() in metric_slide["insight"].lower() for placeholder in OLD_INSIGHT_PLACEHOLDERS)
    assert len(summary["ai_summary"]) <= 520
    assert len(summary["recommendation"]) <= 220
    assert not any(placeholder.lower() in summary["ai_summary"].lower() for placeholder in OLD_INSIGHT_PLACEHOLDERS)


def test_build_5_blocks_instagram_business_can_return_na_without_breaking():
    context = _base_context(integration_type="instagram_business")
    context["impressions"] = None
    context["report_inputs"]["impressions"] = None
    context["report_inputs"]["impressions_daily"] = []
    context["engagement"] = None
    context["page_views"] = None
    context["report_inputs"]["engagement"] = None
    context["report_inputs"]["page_views"] = None
    context["report_inputs"]["engagement_daily"] = []
    context["report_inputs"]["page_views_daily"] = []
    context["report_inputs"]["unavailable_metrics"] = {
        "impressions": "not_returned_by_meta",
        "engagement": "missing_permission",
        "page_views": "missing_permission",
        "profile_views": "missing_permission",
    }
    blocks = build_5_blocks(context)
    impressions = json.loads(blocks[2]["data_json"])
    engagement = json.loads(blocks[3]["data_json"])
    summary = json.loads(blocks[4]["data_json"])

    assert impressions["metric_key"] == "impressions"
    assert impressions["formatted_total"] == "N/A"

    assert engagement["metric_key"] == "engagement"
    assert engagement["total"] is None
    assert engagement["formatted_total"] == "N/A"
    assert engagement["is_available"] is False
    assert engagement["unavailable_message"] == "Dato no disponible en este momento con los permisos actuales de Meta."

    assert summary["metrics_summary"]["page_views"]["value"] is None
    assert summary["metrics_summary"]["page_views"]["formatted_value"] == "N/A"
    assert summary["metrics_summary"]["engagement"]["value"] is None


def test_build_5_blocks_engagement_uses_daily_series_when_available():
    context = _base_context(integration_type="instagram_business")
    context["report_inputs"]["daily_engagement"] = [
        {"date": "2026-05-15", "value": 11},
        {"date": "2026-05-16", "value": 9},
    ]
    blocks = build_5_blocks(context)
    engagement = json.loads(blocks[3]["data_json"])
    assert engagement["metric_key"] == "engagement"
    assert engagement["daily_series"][0]["value"] == 11
    assert "insight_full" in engagement


def test_build_5_blocks_summary_uses_page_views_daily_when_available():
    context = _base_context(integration_type="facebook_pages")
    context["page_views"] = None
    context["report_inputs"]["page_views"] = None
    context["report_inputs"]["page_views_daily"] = [
        {"date": "2026-05-15", "value": 10},
        {"date": "2026-05-16", "value": 20},
    ]
    blocks = build_5_blocks(context)
    summary = json.loads(blocks[4]["data_json"])
    assert summary["metrics_summary"]["page_views"]["value"] == 30
    assert summary["metrics_summary"]["page_views"]["formatted_value"] == "30"


def test_build_5_blocks_summary_preserves_zero_page_views():
    context = _base_context(integration_type="facebook_pages")
    context["page_views"] = 0
    context["report_inputs"]["page_views"] = 0
    context["report_inputs"]["page_views_daily"] = [
        {"date": "2026-05-15", "value": 0},
        {"date": "2026-05-16", "value": 0},
    ]
    blocks = build_5_blocks(context)
    summary = json.loads(blocks[4]["data_json"])
    assert summary["metrics_summary"]["page_views"]["is_available"] is True
    assert summary["metrics_summary"]["page_views"]["value"] == 0
    assert summary["metrics_summary"]["page_views"]["formatted_value"] == "0"


def test_build_5_blocks_engagement_can_be_calculated_from_components():
    context = _base_context(integration_type="facebook_pages")
    context["engagement"] = None
    context["report_inputs"]["engagement"] = None
    context["report_inputs"]["likes"] = 10
    context["report_inputs"]["comments"] = 5
    context["report_inputs"]["shares"] = 3
    context["report_inputs"]["saves"] = 2
    context["report_inputs"]["reactions"] = 8
    context["report_inputs"]["link_clicks"] = 4
    context["report_inputs"]["engagement_daily"] = []
    context["report_inputs"]["interactions_daily"] = [
        {"date": "2026-05-15", "value": 7},
        {"date": "2026-05-16", "value": 5},
    ]
    blocks = build_5_blocks(context)
    engagement = json.loads(blocks[3]["data_json"])
    assert engagement["total"] == 32
    assert engagement["formatted_total"] == "32"
    assert engagement["metric_source"] == "calculated_from_components"
    assert engagement["daily_series"][0]["value"] == 7


def test_build_5_blocks_daily_series_preserves_last_period_date_when_present():
    context = _base_context(integration_type="facebook_pages")
    context["report_timeframe"] = {"label": "May 15-21", "since": "2026-05-15", "until": "2026-05-21"}
    context["reach_chart_data"]["points"] = [
        {"date": "2026-05-15", "value": 10},
        {"date": "2026-05-20", "value": 20},
        {"date": "2026-05-21", "value": 30},
    ]
    context["report_inputs"]["reach_daily"] = context["reach_chart_data"]["points"]
    blocks = build_5_blocks(context)
    reach = json.loads(blocks[1]["data_json"])
    assert reach["daily_series"][-1]["date"] == "2026-05-21"
    assert reach["highest_day"]["date"] == "2026-05-21"


def test_build_5_blocks_summary_metrics_use_renderable_primitives():
    blocks = build_5_blocks(_base_context(integration_type="instagram_business"))
    summary = json.loads(blocks[4]["data_json"])
    metrics_summary = summary["metrics_summary"]
    assert metrics_summary["reach"] == {
        "label": "Reach",
        "value": 44851,
        "formatted_value": "44,851",
        "is_available": True,
        "description": "Total reach",
    }
    assert metrics_summary["impressions"]["label"] == "Impressions"
    assert metrics_summary["engagement"]["value"] == 320
    assert metrics_summary["followers"]["value"] == 1200
    assert metrics_summary["page_views"]["value"] == 5748
    assert isinstance(metrics_summary["page_views"]["formatted_value"], str)
    assert not isinstance(metrics_summary["page_views"]["value"], dict)


def test_build_5_blocks_metric_insights_are_human_and_actionable():
    blocks = build_5_blocks(_base_context(integration_type="facebook_pages"))
    reach = json.loads(blocks[1]["data_json"])
    impressions = json.loads(blocks[2]["data_json"])
    engagement = json.loads(blocks[3]["data_json"])
    summary = json.loads(blocks[4]["data_json"])

    assert "alcance" in reach["insight"].lower()
    assert "meta did not return impressions" in impressions["insight"].lower()
    assert "engagement" in engagement["insight"].lower()
    assert any(word in reach["insight"].lower() for word in ("conviene", "reforzar", "visibilidad"))
    assert any(word in engagement["insight"].lower() for word in ("analiza", "respuesta", "acción"))
    assert "Reach cerró" in summary["ai_summary"]
    assert "page views" in summary["ai_summary"].lower()
    assert not any(placeholder.lower() in summary["text"].lower() for placeholder in OLD_INSIGHT_PLACEHOLDERS)


def test_build_5_blocks_facebook_pages_keeps_reach_na_and_impressions_on_own_slide():
    context = _base_context(integration_type="facebook_pages")
    context["reach"] = None
    context["reach_chart_data"]["points"] = []
    context["report_inputs"]["reach"] = None
    context["report_inputs"]["reach_daily"] = []
    context["report_inputs"]["unavailable_metrics"] = {
        "reach": "not_returned_by_meta",
    }
    context["report_inputs"]["normalized_report_metrics"] = {
        "impressions_total": 546,
        "daily_impressions": [
            {"date": "2026-05-19", "value": 100},
            {"date": "2026-05-20", "value": 80},
            {"date": "2026-05-21", "value": 76},
            {"date": "2026-05-22", "value": 90},
            {"date": "2026-05-23", "value": 70},
            {"date": "2026-05-24", "value": 65},
            {"date": "2026-05-25", "value": 65},
        ],
    }
    blocks = build_5_blocks(context)
    reach = json.loads(blocks[1]["data_json"])
    impressions = json.loads(blocks[2]["data_json"])
    summary = json.loads(blocks[4]["data_json"])

    assert reach["title"] == "Reach"
    assert reach["metric_key"] == "reach"
    assert reach["formatted_total"] == "N/A"
    assert reach["unavailable_message"] == "Meta did not return unique reach for the selected period."
    assert reach["insight"] == "Meta did not return unique reach for the selected period."

    assert impressions["title"] == "Impressions"
    assert impressions["label"] == "Total Impressions"
    assert impressions["metric_key"] == "impressions"
    assert impressions["formatted_total"] == "546"
    assert impressions["is_available"] is True
    assert impressions["chart"]["metric"] == "impressions"
    assert impressions["chart"]["label"] == "Total Impressions"
    assert impressions["daily_series"][0]["date"] == "2026-05-19"

    assert summary["metrics_summary"]["reach"]["label"] == "Reach"
    assert summary["metrics_summary"]["reach"]["value"] is None
    assert summary["metrics_summary"]["reach"]["formatted_value"] == "N/A"
    assert summary["metrics_summary"]["impressions"]["value"] == 546
    assert "Impressions registró 546" in summary["ai_summary"]
    assert "unique reach for the selected period" in summary["ai_summary"]


def test_build_5_blocks_facebook_pages_shows_na_when_reach_and_impressions_missing():
    context = _base_context(integration_type="facebook_pages")
    context["reach"] = None
    context["reach_chart_data"]["points"] = []
    context["report_inputs"]["reach"] = None
    context["report_inputs"]["reach_daily"] = []
    context["report_inputs"]["normalized_report_metrics"] = {}
    context["report_inputs"]["unavailable_metrics"] = {
        "reach": "not_returned_by_meta",
        "impressions": "not_returned_by_meta",
    }
    blocks = build_5_blocks(context)
    reach = json.loads(blocks[1]["data_json"])
    impressions = json.loads(blocks[2]["data_json"])

    assert reach["title"] == "Reach"
    assert reach["metric_key"] == "reach"
    assert reach["formatted_total"] == "N/A"
    assert reach["is_available"] is False
    assert reach["unavailable_message"] == "Meta did not return unique reach for the selected period."
    assert reach["insight"] == "Meta did not return unique reach for the selected period."
    assert impressions["formatted_total"] == "N/A"
    assert impressions["unavailable_message"] == "Meta did not return impressions for the selected period."


def test_build_5_blocks_summary_includes_real_post_metrics_when_available():
    context = _base_context(integration_type="facebook_pages")
    context["report_inputs"]["recent_posts"] = [
        {"id": "1", "message": "Post one", "created_time": "2026-05-15", "reactions": 10, "comments": 3, "shares": 2},
        {"id": "2", "message": "Post two", "created_time": "2026-05-16", "reactions": 6, "comments": 4, "shares": 1},
    ]
    blocks = build_5_blocks(context)
    summary = json.loads(blocks[4]["data_json"])

    assert summary["metrics_summary"]["posts_analyzed"]["value"] == 2
    assert summary["metrics_summary"]["reactions"]["value"] == 16
    assert summary["metrics_summary"]["comments"]["value"] == 7
    assert summary["metrics_summary"]["shares"]["value"] == 3
    assert summary["metrics_summary"]["top_post"]["label"] == "Top Post"


def test_build_5_blocks_branding_appears_on_all_slides():
    context = _base_context(integration_type="facebook_pages")
    context["branding"] = {
        "brand_name": "Agency",
        "brand_logo_url": "https://example.com/logo.png",
        "resolved_brand_name": "Agency",
        "resolved_logo_url": "https://example.com/logo.png",
    }
    blocks = build_5_blocks(context)
    for block in blocks:
        data = json.loads(block["data_json"])
        assert data["branding"]["resolved_brand_name"] == "Agency"
        assert data["branding"]["resolved_logo_url"] == "https://example.com/logo.png"
