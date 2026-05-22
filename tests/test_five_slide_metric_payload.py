from __future__ import annotations

import json
import os

os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/measurable_five_slide_test.db?check_same_thread=false")
os.environ.setdefault("JWT_SECRET", "test-jwt-secret")

from app.main import build_5_blocks, extractDailyMetricSeries, truncateInsightForSlide


def _base_context(*, integration_type: str) -> dict:
    return {
        "title": "Executive report",
        "plan": "core",
        "report_timeframe": {"label": "Last 28 days", "since": "2026-05-01", "until": "2026-05-28"},
        "page_name": "Acme Account",
        "followers": 1200,
        "reach": 44851,
        "engagement": 320,
        "impressions": 90120,
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
        "impressions_slide_payload": {
            "impressions_daily": [
                {"date": "2026-05-15", "value": 4567},
                {"date": "2026-05-16", "value": 3210},
            ],
            "insight_text": "Impressions moved unevenly but held strong overall.",
        },
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
            "impressions_daily": [
                {"date": "2026-05-15", "value": 4567},
                {"date": "2026-05-16", "value": 3210},
            ],
            "engagement": 320,
            "impressions": 90120,
            "reach": 44851,
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
            "time_series": {
                "page_impressions": [
                    {"date": "2026-05-15", "value": "8"},
                    {"date": "2026-05-16", "value": 0},
                ]
            },
            "values": {
                "total_interactions": [
                    {"date": "2026-05-15", "value": "4"},
                    {"date": "2026-05-16", "value": "0"},
                ]
            },
        }
    }
    reach = extractDailyMetricSeries(dataset, "reach")
    impressions = extractDailyMetricSeries(dataset, "impressions")
    engagement = extractDailyMetricSeries(dataset, "engagement")
    assert reach == [
        {"date": "2026-05-15", "label": "May 15", "value": 0.0},
        {"date": "2026-05-16", "label": "May 16", "value": 12.0},
    ]
    assert impressions == [
        {"date": "2026-05-15", "label": "May 15", "value": 8.0},
        {"date": "2026-05-16", "label": "May 16", "value": 0.0},
    ]
    assert engagement == [
        {"date": "2026-05-15", "label": "May 15", "value": 4.0},
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
                "impressions_daily": [
                    {"date": "2026-05-15", "value": 600},
                    {"date": "2026-05-16", "value": 767},
                ],
            },
        }
    }
    assert extractDailyMetricSeries(dataset, "reach") == [
        {"date": "2026-05-15", "label": "May 15", "value": 1200},
        {"date": "2026-05-16", "label": "May 16", "value": 900},
    ]
    assert extractDailyMetricSeries(dataset, "impressions") == [
        {"date": "2026-05-15", "label": "May 15", "value": 600},
        {"date": "2026-05-16", "label": "May 16", "value": 767},
    ]


def test_truncate_insight_for_slide_limits_to_280_chars():
    long_text = " ".join(["This is a long insight sentence."] * 20)
    short_text, full_text = truncateInsightForSlide(long_text, limit=280)
    assert len(short_text) <= 280
    assert len(full_text) > 280


def test_build_5_blocks_metric_slides_for_facebook_pages():
    blocks = build_5_blocks(_base_context(integration_type="facebook_pages"))
    assert len(blocks) == 5
    cover = json.loads(blocks[0]["data_json"])
    reach = json.loads(blocks[1]["data_json"])
    impressions = json.loads(blocks[2]["data_json"])
    engagement = json.loads(blocks[3]["data_json"])
    summary = json.loads(blocks[4]["data_json"])

    assert cover["slide_number"] == 1
    assert cover["slide_type"] == "cover"
    assert reach["slide_number"] == 2
    assert reach["slide_type"] == "metric"
    assert reach["metric_key"] == "reach"
    assert reach["metric_label"] == "Alcance"
    assert reach["metric_label_en"] == "Reach"
    assert reach["daily_series"][0]["date"] == "2026-05-15"
    assert reach["highest_day"]["value"] == 1234
    assert reach["lowest_day"]["value"] == 900

    assert impressions["slide_number"] == 3
    assert impressions["metric_key"] == "impressions"
    assert impressions["metric_label"] == "Impresiones"
    assert impressions["daily_series"][1]["value"] == 3210

    assert engagement["slide_number"] == 4
    assert engagement["metric_key"] == "engagement"
    assert engagement["daily_series"][0]["value"] == 80
    assert len(engagement["insight_short"]) <= 280
    assert summary["slide_number"] == 5
    assert summary["slide_type"] == "summary"
    assert "metrics_summary" in summary
    assert len(summary["ai_summary"]) <= 400
    assert summary["metrics_summary"]["reach"]["value"] == 44851
    assert summary["metrics_summary"]["reach"]["formatted_value"] == "44,851"
    assert summary["metrics_summary"]["reach"]["description"] == "Total reach"
    assert isinstance(summary["metrics_summary"]["reach"]["formatted_value"], str)
    assert not isinstance(summary["metrics_summary"]["reach"]["value"], dict)


def test_build_5_blocks_metric_slides_for_instagram_business():
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


def test_build_5_blocks_metric_slide_without_daily_series_keeps_empty_array():
    context = _base_context(integration_type="facebook_pages")
    context["impressions"] = 0
    context["report_inputs"]["impressions"] = 0
    context["impressions_slide_payload"] = {"impressions_daily": []}
    context["report_inputs"]["impressions_daily"] = []
    blocks = build_5_blocks(context)
    impressions = json.loads(blocks[2]["data_json"])
    assert impressions["total"] == 0
    assert impressions["daily_series"] == []
    assert impressions["daily_series_reason"] == "daily_series_unavailable_from_source"


def test_build_5_blocks_zero_daily_series_is_preserved_and_available():
    context = _base_context(integration_type="facebook_pages")
    context["impressions"] = 0
    context["report_inputs"]["impressions"] = 0
    context["impressions_slide_payload"] = {
        "impressions_daily": [
            {"date": "2026-05-15", "value": 0},
            {"date": "2026-05-16", "value": 0},
        ]
    }
    context["report_inputs"]["impressions_daily"] = [
        {"date": "2026-05-15", "value": 0},
        {"date": "2026-05-16", "value": 0},
    ]
    blocks = build_5_blocks(context)
    impressions = json.loads(blocks[2]["data_json"])
    assert impressions["daily_series"] == [
        {"date": "2026-05-15", "label": "May 15", "value": 0},
        {"date": "2026-05-16", "label": "May 16", "value": 0},
    ]
    assert impressions["daily_series_reason"] == ""


def test_build_5_blocks_impressions_discards_all_zero_series_when_total_is_positive():
    context = _base_context(integration_type="facebook_pages")
    context["impressions"] = 1367
    context["report_inputs"]["impressions"] = 1367
    context["impressions_slide_payload"] = {
        "impressions_daily": [
            {"date": "2026-05-15", "value": 0},
            {"date": "2026-05-16", "value": 0},
        ]
    }
    context["report_inputs"]["impressions_daily"] = [
        {"date": "2026-05-15", "value": 0},
        {"date": "2026-05-16", "value": 0},
    ]
    blocks = build_5_blocks(context)
    impressions = json.loads(blocks[2]["data_json"])
    assert impressions["total"] == 1367
    assert impressions["daily_series"] == []
    assert impressions["highest_day"] == {}
    assert impressions["lowest_day"] == {}
    assert impressions["daily_series_reason"] == "daily_series_unavailable_from_source"


def test_build_5_blocks_cover_branding_falls_back_to_measurable_when_missing():
    context = _base_context(integration_type="instagram_business")
    blocks = build_5_blocks(context)
    cover = json.loads(blocks[0]["data_json"])
    assert cover["branding"]["resolved_brand_name"] == "Measurableapp.com Report Generator"
    assert cover["resolved_brand_name"] == "Measurableapp.com Report Generator"
    assert bool(cover["branding"]["resolved_logo_url"])


def test_build_5_blocks_engagement_can_be_calculated_from_interactions():
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
    assert engagement["daily_series"][0]["value"] == 7


def test_build_5_blocks_summary_metrics_summary_uses_renderable_primitives():
    blocks = build_5_blocks(_base_context(integration_type="instagram_business"))
    summary = json.loads(blocks[4]["data_json"])
    metrics_summary = summary["metrics_summary"]
    assert metrics_summary["reach"] == {
        "label": "Reach",
        "value": 44851,
        "formatted_value": "44,851",
        "description": "Total reach",
    }
    assert metrics_summary["impressions"]["value"] == 90120
    assert metrics_summary["impressions"]["formatted_value"] == "90,120"
    assert isinstance(metrics_summary["engagement"]["formatted_value"], str)
    assert not isinstance(metrics_summary["engagement"]["value"], dict)
