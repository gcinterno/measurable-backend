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
        "engagement": 320,
        "page_views": 5748,
        "organic_impressions_total": 10187,
        "summary": "Summary",
        "recent_posts_summary": "Posts summary",
        "ai_summary": "AI summary",
        "general_insights_slide_payload": {},
        "report_inputs": {
            "integration_type": integration_type,
            "daily_organic_impressions": [
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
            "organic_impressions_total": 10187,
            "followers": 1200,
            "fans_total": 1190,
            "reactions_total": 342,
        },
        "branding": {},
        "requested_slides": 5,
    }


def test_extract_daily_metric_series_normalizes_nested_sources_and_zero_values():
    dataset = {
        "report_inputs": {
            "integration_type": "facebook_pages",
            "daily_metrics": {
                "page_posts_impressions_organic": [
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
    organic_impressions = extractDailyMetricSeries(dataset, "organic_impressions")
    assert reach == []
    assert organic_impressions == [
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
    organic_impressions = json.loads(blocks[1]["data_json"])
    engagement = json.loads(blocks[2]["data_json"])
    page_views = json.loads(blocks[3]["data_json"])
    summary = json.loads(blocks[4]["data_json"])

    assert cover["slide_number"] == 1
    assert cover["slide_type"] == "cover"
    assert cover["text"] == "Facebook Pages Report - Summary & Insights"
    assert cover["page_name"] == "Acme Account"
    assert cover["platform"] == "Facebook Pages"

    assert organic_impressions["slide_number"] == 2
    assert organic_impressions["slide_type"] == "metric"
    assert organic_impressions["metric_key"] == "organic_impressions"
    assert organic_impressions["metric_label"] == "ORGANIC IMPRESSIONS"
    assert organic_impressions["metric_label_es"] == "Impresiones orgánicas"
    assert organic_impressions["label"] == "TOTAL ORGANIC IMPRESSIONS"
    assert organic_impressions["formatted_total"] == "10,187"
    assert organic_impressions["is_available"] is True
    assert organic_impressions["daily_series"][0]["date"] == "2026-05-15"
    assert organic_impressions["highest_day"]["value"] == 1234
    assert organic_impressions["lowest_day"]["value"] == 900

    assert engagement["slide_number"] == 3
    assert engagement["metric_key"] == "engagement"
    assert engagement["metric_source"] == "direct_meta_metric"
    assert engagement["label"] == "TOTAL ENGAGEMENT"
    assert engagement["daily_series"][0]["value"] == 80
    assert engagement["highest_day"]["value"] == 80

    assert page_views["slide_number"] == 4
    assert page_views["metric_key"] == "page_views"
    assert page_views["label"] == "TOTAL PAGE VIEWS"
    assert page_views["formatted_total"] == "5,748"

    assert summary["slide_number"] == 5
    assert summary["slide_type"] == "summary"
    assert set(summary["metrics_summary"].keys()) == {"organic_impressions", "engagement", "followers", "page_views", "fans", "reactions"}
    assert summary["metrics_summary"]["organic_impressions"]["value"] == 10187
    assert summary["metrics_summary"]["engagement"]["value"] == 320
    assert summary["metrics_summary"]["followers"]["value"] == 1200
    assert summary["metrics_summary"]["page_views"]["value"] == 5748
    assert summary["metrics_summary"]["fans"]["value"] == 1190
    assert summary["metrics_summary"]["reactions"]["value"] == 342

    for metric_slide in (organic_impressions, engagement, page_views):
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
    context["organic_impressions_total"] = None
    context["report_inputs"]["organic_impressions_total"] = None
    context["report_inputs"]["daily_organic_impressions"] = []
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
    organic_impressions = json.loads(blocks[1]["data_json"])
    engagement = json.loads(blocks[2]["data_json"])
    summary = json.loads(blocks[4]["data_json"])

    assert organic_impressions["metric_key"] == "organic_impressions"
    assert organic_impressions["formatted_total"] == "N/A"

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
    engagement = json.loads(blocks[2]["data_json"])
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
    engagement = json.loads(blocks[2]["data_json"])
    assert engagement["total"] == 32
    assert engagement["formatted_total"] == "32"
    assert engagement["metric_source"] == "calculated_from_components"
    assert engagement["daily_series"][0]["value"] == 7


def test_build_5_blocks_daily_series_preserves_last_period_date_when_present():
    context = _base_context(integration_type="facebook_pages")
    context["report_timeframe"] = {"label": "May 15-21", "since": "2026-05-15", "until": "2026-05-21"}
    context["report_inputs"]["daily_organic_impressions"] = [
        {"date": "2026-05-15", "value": 10},
        {"date": "2026-05-20", "value": 20},
        {"date": "2026-05-21", "value": 30},
    ]
    blocks = build_5_blocks(context)
    organic_impressions = json.loads(blocks[1]["data_json"])
    assert organic_impressions["daily_series"][-1]["date"] == "2026-05-21"
    assert organic_impressions["highest_day"]["date"] == "2026-05-21"


def test_build_5_blocks_summary_metrics_use_renderable_primitives():
    blocks = build_5_blocks(_base_context(integration_type="instagram_business"))
    summary = json.loads(blocks[4]["data_json"])
    metrics_summary = summary["metrics_summary"]
    assert metrics_summary["organic_impressions"] == {
        "label": "ORGANIC IMPRESSIONS",
        "value": 10187,
        "formatted_value": "10,187",
        "is_available": True,
        "description": "Organic post impressions",
    }
    assert metrics_summary["engagement"]["value"] == 320
    assert metrics_summary["followers"]["value"] == 1200
    assert metrics_summary["page_views"]["value"] == 5748
    assert isinstance(metrics_summary["page_views"]["formatted_value"], str)
    assert not isinstance(metrics_summary["page_views"]["value"], dict)


def test_build_5_blocks_metric_insights_are_human_and_actionable():
    blocks = build_5_blocks(_base_context(integration_type="facebook_pages"))
    organic_impressions = json.loads(blocks[1]["data_json"])
    engagement = json.loads(blocks[2]["data_json"])
    page_views = json.loads(blocks[3]["data_json"])
    summary = json.loads(blocks[4]["data_json"])

    assert "impresiones orgánicas" in organic_impressions["insight"].lower()
    assert "engagement" in engagement["insight"].lower()
    assert "page views" in page_views["metric_label"].lower()
    assert any(word in organic_impressions["insight"].lower() for word in ("visibilidad", "orgánica", "reach"))
    assert any(word in engagement["insight"].lower() for word in ("analiza", "respuesta", "acción"))
    assert "Organic Impressions registró" in summary["ai_summary"]
    assert "page views" in summary["ai_summary"].lower()
    assert not any(placeholder.lower() in summary["text"].lower() for placeholder in OLD_INSIGHT_PLACEHOLDERS)


def test_build_5_blocks_facebook_pages_uses_organic_impressions_on_own_slide():
    context = _base_context(integration_type="facebook_pages")
    context["organic_impressions_total"] = None
    context["report_inputs"]["organic_impressions_total"] = None
    context["report_inputs"]["unavailable_metrics"] = {
        "reach": "not_returned_by_meta",
    }
    context["report_inputs"]["normalized_report_metrics"] = {
        "organic_impressions_total": 546,
        "daily_organic_impressions": [
            {"date": "2026-05-19", "value": 100},
            {"date": "2026-05-20", "value": 80},
            {"date": "2026-05-21", "value": 76},
            {"date": "2026-05-22", "value": 90},
            {"date": "2026-05-23", "value": 70},
            {"date": "2026-05-24", "value": 65},
            {"date": "2026-05-25", "value": 65},
        ],
    }
    context["report_inputs"]["daily_organic_impressions"] = context["report_inputs"]["normalized_report_metrics"]["daily_organic_impressions"]
    blocks = build_5_blocks(context)
    organic_impressions = json.loads(blocks[1]["data_json"])
    summary = json.loads(blocks[4]["data_json"])

    assert organic_impressions["title"] == "ORGANIC IMPRESSIONS"
    assert organic_impressions["label"] == "TOTAL ORGANIC IMPRESSIONS"
    assert organic_impressions["metric_key"] == "organic_impressions"
    assert organic_impressions["formatted_total"] == "546"
    assert organic_impressions["is_available"] is True
    assert organic_impressions["chart"]["metric"] == "organic_impressions"
    assert organic_impressions["chart"]["label"] == "TOTAL ORGANIC IMPRESSIONS"
    assert organic_impressions["daily_series"][0]["date"] == "2026-05-19"

    assert summary["metrics_summary"]["organic_impressions"]["label"] == "ORGANIC IMPRESSIONS"
    assert summary["metrics_summary"]["organic_impressions"]["value"] == 546
    assert "Organic Impressions registró 546" in summary["ai_summary"]
    assert "unique reach for the selected period" in summary["ai_summary"]


def test_build_5_blocks_facebook_pages_shows_na_when_organic_impressions_missing():
    context = _base_context(integration_type="facebook_pages")
    context["organic_impressions_total"] = None
    context["report_inputs"]["normalized_report_metrics"] = {}
    context["report_inputs"]["organic_impressions_total"] = None
    context["report_inputs"]["daily_organic_impressions"] = []
    context["report_inputs"]["unavailable_metrics"] = {
        "reach": "not_returned_by_meta",
        "organic_impressions": "not_returned_by_meta",
    }
    blocks = build_5_blocks(context)
    organic_impressions = json.loads(blocks[1]["data_json"])

    assert organic_impressions["title"] == "ORGANIC IMPRESSIONS"
    assert organic_impressions["metric_key"] == "organic_impressions"
    assert organic_impressions["formatted_total"] == "N/A"
    assert organic_impressions["is_available"] is False
    assert organic_impressions["unavailable_message"] == "Meta did not return organic post impressions for the selected period."
    assert organic_impressions["insight"] == "Meta did not return organic post impressions for the selected period."


def test_build_5_blocks_summary_includes_real_post_metrics_when_available():
    context = _base_context(integration_type="facebook_pages")
    context["report_inputs"]["recent_posts"] = [
        {"id": "1", "message": "Post one", "created_time": "2026-05-15", "reactions": 10, "comments": 3, "shares": 2},
        {"id": "2", "message": "Post two", "created_time": "2026-05-16", "reactions": 6, "comments": 4, "shares": 1},
    ]
    blocks = build_5_blocks(context)
    summary = json.loads(blocks[4]["data_json"])

    assert "posts_analyzed" not in summary["metrics_summary"]
    assert summary["metrics_summary"]["reactions"]["value"] == 342


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
