from __future__ import annotations

import json
import os

os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/measurable_five_slide_test.db?check_same_thread=false")
os.environ.setdefault("JWT_SECRET", "test-jwt-secret")

from app.main import (
    _ensure_facebook_pages_five_slide_structure,
    _validate_report_blocks_for_sources,
    INSTAGRAM_BUSINESS_FIVE_SLIDE_TYPES,
    build_5_blocks,
    build_instagram_business_5_blocks,
    extractDailyMetricSeries,
    truncateInsightForSlide,
)


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
            "daily_engagement": [
                {"date": "2026-05-15", "value": 80},
                {"date": "2026-05-16", "value": 40},
            ],
            "daily_page_views": [
                {"date": "2026-05-15", "value": 3300},
                {"date": "2026-05-16", "value": 2448},
            ],
            "organic_impressions_total": 10187,
            "engagement_total": 320,
            "page_views_total": 5748,
            "followers_total": 1200,
            "fans_total": 1190,
            "reactions_total": 342,
            "normalized_report_metrics": {
                "organic_impressions_total": 10187,
                "daily_organic_impressions": [
                    {"date": "2026-05-15", "value": 1234},
                    {"date": "2026-05-16", "value": 900},
                ],
                "engagement_total": 320,
                "daily_engagement": [
                    {"date": "2026-05-15", "value": 80},
                    {"date": "2026-05-16", "value": 40},
                ],
                "page_views_total": 5748,
                "daily_page_views": [
                    {"date": "2026-05-15", "value": 3300},
                    {"date": "2026-05-16", "value": 2448},
                ],
                "followers_total": 1200,
                "fans_total": 1190,
                "reactions_total": 342,
            },
        },
        "branding": {},
        "requested_slides": 5,
    }


def _instagram_context() -> dict:
    return {
        "title": "Instagram executive report",
        "plan": "core",
        "report_timeframe": {"label": "Last 28 days", "since": "2026-05-01", "until": "2026-05-28"},
        "page_name": "Acme Instagram",
        "reach": 9400,
        "engagement": 785,
        "impressions": 12100,
        "report_inputs": {
            "integration_type": "instagram_business",
            "provider": "instagram_business_login",
            "account_id": "17841400000000000",
            "account_name": "Acme Instagram",
            "username": "acmeig",
            "followers": 11242,
            "followers_count": 11242,
            "followers_total": 11242,
            "reach": 9400,
            "views": 12100,
            "impressions": 12100,
            "engagement": 785,
            "total_interactions": 785,
            "accounts_engaged": 620,
            "profile_views": 144,
            "profile_visits": 144,
            "likes": 520,
            "comments": 41,
            "shares": 33,
            "saves": 27,
            "replies": 9,
            "reach_daily": [
                {"date": "2026-05-15", "value": 3000},
                {"date": "2026-05-16", "value": 6400},
            ],
            "views_daily": [
                {"date": "2026-05-15", "value": 6100},
                {"date": "2026-05-16", "value": 6000},
            ],
            "daily_engagement": [
                {"date": "2026-05-15", "value": 310},
                {"date": "2026-05-16", "value": 475},
            ],
            "top_content": [
                {
                    "post_id": "ig-post-1",
                    "created_time": "2026-05-16T12:00:00+0000",
                    "message_preview": "Top reel",
                    "permalink_url": "https://instagram.com/p/1",
                    "media_type": "REELS",
                    "reach": 4200,
                    "views": 6200,
                    "engagement_total": 490,
                    "score": 490,
                }
            ],
            "unavailable_metrics": {},
            "normalized_report_metrics": {
                "followers_total": 11242,
                "viewers_total": 9400,
                "viewers_daily": [
                    {"date": "2026-05-15", "value": 3000},
                    {"date": "2026-05-16", "value": 6400},
                ],
                "views_total": 12100,
                "views_daily": [
                    {"date": "2026-05-15", "value": 6100},
                    {"date": "2026-05-16", "value": 6000},
                ],
                "interactions_total": 785,
                "interactions_daily": [
                    {"date": "2026-05-15", "value": 310},
                    {"date": "2026-05-16", "value": 475},
                ],
                "accounts_engaged_total": 620,
                "total_interactions_total": 785,
                "likes_total": 520,
                "comments_total": 41,
                "shares_total": 33,
                "saves_total": 27,
                "replies_total": 9,
                "page_visits_total": 144,
            },
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
    assert organic_impressions["slide_type"] == "organic_impressions_overview"
    assert organic_impressions["metric_key"] == "organic_impressions"
    assert organic_impressions["metric_label"] == "Organic Impressions"
    assert organic_impressions["metric_label_es"] == "Impresiones orgánicas"
    assert organic_impressions["title"] == "ORGANIC VISIBILITY"
    assert organic_impressions["label"] == "TOTAL ORGANIC IMPRESSIONS"
    assert organic_impressions["formatted_total"] == "10,187"
    assert organic_impressions["is_available"] is True
    assert organic_impressions["provider"] == "facebook_pages"
    assert organic_impressions["raw_metric_name"] == "page_posts_impressions_organic"
    assert organic_impressions["normalized_field"] == "organic_impressions_total"
    assert organic_impressions["availability_status"] == "available"
    assert organic_impressions["source_metrics_used"] == ["page_posts_impressions_organic"]
    assert organic_impressions["daily_series"][0]["date"] == "2026-05-15"
    assert organic_impressions["highest_day"]["value"] == 1234
    assert organic_impressions["lowest_day"]["value"] == 900

    assert engagement["slide_number"] == 3
    assert engagement["slide_type"] == "engagement_overview"
    assert engagement["metric_key"] == "engagement"
    assert engagement["metric_source"] == "page_post_engagements"
    assert engagement["raw_metric_name"] == "page_post_engagements"
    assert engagement["normalized_field"] == "engagement_total"
    assert engagement["label"] == "TOTAL ENGAGEMENT"
    assert engagement["daily_series"][0]["value"] == 80
    assert engagement["highest_day"]["value"] == 80

    assert page_views["slide_number"] == 4
    assert page_views["slide_type"] == "page_views_overview"
    assert page_views["metric_key"] == "page_views"
    assert page_views["metric_source"] == "page_views_total"
    assert page_views["raw_metric_name"] == "page_views_total"
    assert page_views["normalized_field"] == "page_views_total"
    assert page_views["label"] == "TOTAL PAGE VIEWS"
    assert page_views["formatted_total"] == "5,748"

    assert summary["slide_number"] == 5
    assert summary["slide_type"] == "executive_summary"
    assert summary["title"] == "Executive Summary"
    assert set(summary["metrics_summary"].keys()) == {"organic_impressions", "engagement", "followers", "page_views", "fans", "reactions"}
    assert summary["metrics_summary"]["organic_impressions"]["value"] == 10187
    assert summary["metrics_summary"]["engagement"]["value"] == 320
    assert summary["metrics_summary"]["followers"]["value"] == 1200
    assert summary["metrics_summary"]["page_views"]["value"] == 5748
    assert summary["metrics_summary"]["fans"]["value"] == 1190
    assert summary["metrics_summary"]["reactions"]["value"] == 342
    assert summary["metrics_summary"]["page_views"]["raw_metric_name"] == "page_views_total"
    assert summary["metrics_summary"]["organic_impressions"]["raw_metric_name"] == "page_posts_impressions_organic"
    assert summary["provider"] == "facebook_pages"
    assert "reach_overview" not in [json.loads(block["data_json"]).get("slide_type") for block in blocks]

    for metric_slide in (organic_impressions, engagement, page_views):
        assert metric_slide["insight_tone"] == "executive_ai"
        assert metric_slide["insight_max_chars"] == 260
        assert len(metric_slide["insight_short"]) <= 260
        assert len(metric_slide["insight"]) <= 420
        assert not any(placeholder.lower() in metric_slide["insight"].lower() for placeholder in OLD_INSIGHT_PLACEHOLDERS)
    assert len(summary["ai_summary"]) <= 520
    assert len(summary["recommendation"]) <= 220
    assert not any(placeholder.lower() in summary["ai_summary"].lower() for placeholder in OLD_INSIGHT_PLACEHOLDERS)


def test_build_instagram_business_5_blocks_generates_instagram_only_structure():
    blocks = build_instagram_business_5_blocks(_instagram_context())
    payloads = [json.loads(block["data_json"]) for block in blocks]
    payload_text = json.dumps(payloads)

    assert len(blocks) == 5
    assert [payload["semantic_name"] for payload in payloads] == INSTAGRAM_BUSINESS_FIVE_SLIDE_TYPES
    assert [payload["slide_type"] for payload in payloads] == INSTAGRAM_BUSINESS_FIVE_SLIDE_TYPES
    assert _validate_report_blocks_for_sources(
        selected_sources=["instagram_business"],
        block_specs=blocks,
    ) is blocks

    assert payloads[0]["text"] == "Instagram Business Report - Summary & Insights"
    assert payloads[0]["platform"] == "Instagram Business"
    assert payloads[1]["metric_key"] == "reach"
    assert payloads[2]["metric_key"] == "views"
    assert payloads[3]["metric_key"] == "engagement"
    assert set(payloads[4]["metrics_summary"].keys()) >= {
        "followers",
        "reach",
        "views",
        "engagement",
        "accounts_engaged",
        "total_interactions",
        "profile_views",
    }
    assert payloads[4]["top_content"][0]["post_id"] == "ig-post-1"
    assert "Views / Impressions" not in payload_text
    assert "VIEWS / IMPRESSIONS" not in payload_text
    assert "Facebook Pages" not in payload_text
    assert "Organic Visibility" not in payload_text
    assert "page_posts_impressions_organic" not in payload_text
    assert "Page Views" not in payload_text
    assert '"fans"' not in payload_text.lower()


def test_instagram_business_unavailable_metric_does_not_fallback_to_facebook_metric():
    context = _instagram_context()
    context["report_inputs"]["views"] = None
    context["report_inputs"]["impressions"] = None
    context["impressions"] = None
    context["report_inputs"]["views_daily"] = []
    context["report_inputs"]["normalized_report_metrics"]["views_total"] = None
    context["report_inputs"]["normalized_report_metrics"]["views_daily"] = []
    context["report_inputs"]["normalized_report_metrics"]["impressions_total"] = None
    context["report_inputs"]["normalized_report_metrics"]["impressions_daily"] = []
    context["report_inputs"]["page_views_total"] = 9999
    context["report_inputs"]["daily_page_views"] = [{"date": "2026-05-15", "value": 9999}]
    context["report_inputs"]["normalized_report_metrics"]["page_views_total"] = 9999
    context["report_inputs"]["normalized_report_metrics"]["daily_page_views"] = [
        {"date": "2026-05-15", "value": 9999}
    ]
    context["report_inputs"]["unavailable_metrics"] = {"views": "not_returned_by_meta"}

    blocks = build_instagram_business_5_blocks(context)
    views = json.loads(blocks[2]["data_json"])
    summary = json.loads(blocks[4]["data_json"])
    unavailable_summary_keys = [
        key
        for key, value in summary["metrics_summary"].items()
        if value["is_available"] is False
    ]

    assert views["metric_key"] == "views"
    assert views["total"] is None
    assert views["formatted_total"] == "N/A"
    assert views["is_available"] is False
    assert views["availability_status"] == "unavailable"
    assert views["unavailable_reason"] == "not_returned_by_meta"
    assert views["raw_metric_name"] is None
    assert views["source_metrics_used"] == []
    assert summary["metrics_summary"]["views"]["value"] is None
    assert summary["metrics_summary"]["views"]["formatted_value"] == "N/A"
    assert unavailable_summary_keys == ["views"]
    assert "page_views_total" not in json.dumps([views, summary])


def test_instagram_business_views_total_without_series_is_available():
    context = _instagram_context()
    context["report_inputs"]["views"] = 32600
    context["report_inputs"]["views_daily"] = []
    context["report_inputs"]["normalized_report_metrics"]["views_total"] = 32600
    context["report_inputs"]["normalized_report_metrics"]["views_daily"] = []
    context["report_inputs"]["instagram_metric_audit"] = {
        "metrics": {
            "views": {
                "metric_name_requested": "views",
                "metric_type": "total_value",
                "response_shape": "total_value",
            }
        }
    }

    blocks = build_instagram_business_5_blocks(context)
    views = json.loads(blocks[2]["data_json"])

    assert views["metric_key"] == "views"
    assert views["title"] == "VIEWS"
    assert views["metric_label"] == "Views"
    assert views["total"] == 32600
    assert views["value"] == 32600
    assert views["value_available"] is True
    assert views["series_available"] is False
    assert views["is_available"] is True
    assert views["availability"] == "available"
    assert views["metric_type"] == "total_value"
    assert views["daily_series"] == []
    assert views["chart"]["is_available"] is False
    assert views["unavailable_message"] is None
    assert views["daily_series_reason"] == "No daily trend was available for this metric."


def test_instagram_business_views_ai_context_without_series_is_aggregate_only():
    context = _instagram_context()
    context["report_inputs"]["views"] = 32600
    context["report_inputs"]["views_daily"] = []
    context["report_inputs"]["normalized_report_metrics"]["views_total"] = 32600
    context["report_inputs"]["normalized_report_metrics"]["views_daily"] = []
    context["report_inputs"]["instagram_metric_audit"] = {
        "metrics": {
            "views": {
                "metric_name_requested": "views",
                "metric_type": "total_value",
                "response_shape": "total_value",
            }
        }
    }

    blocks = build_instagram_business_5_blocks(context)
    views = json.loads(blocks[2]["data_json"])
    insight_lower = views["insight"].lower()

    assert views["ai_insight_context"] == {
        "metric_key": "views",
        "metric_value": 32600,
        "series_available": False,
        "series": [],
        "availability": "available",
        "metric_type": "total_value",
    }
    assert "serie diaria" not in insight_lower
    assert "daily trend" not in insight_lower
    assert "pico" not in insight_lower
    assert "highest" not in insight_lower
    assert "lowest" not in insight_lower
    assert "mejor día" not in insight_lower
    assert "peor día" not in insight_lower


def test_instagram_business_total_interactions_without_series_is_available():
    context = _instagram_context()
    context["report_inputs"]["daily_engagement"] = []
    context["report_inputs"]["normalized_report_metrics"]["interactions_daily"] = []
    context["report_inputs"]["normalized_report_metrics"]["total_interactions_total"] = 407
    context["report_inputs"]["normalized_report_metrics"]["interactions_total"] = 407
    context["report_inputs"]["total_interactions"] = 407
    context["report_inputs"]["accounts_engaged"] = 285
    context["report_inputs"]["engagement"] = 407
    context["report_inputs"]["instagram_metric_audit"] = {
        "metrics": {
            "engagement": {
                "source_metric": "total_interactions",
                "metric_type": "total_value",
            },
            "total_interactions": {
                "metric_name_requested": "total_interactions",
                "metric_type": "total_value",
                "response_shape": "total_value",
            },
        }
    }

    blocks = build_instagram_business_5_blocks(context)
    engagement = json.loads(blocks[3]["data_json"])
    insight_lower = engagement["insight"].lower()

    assert engagement["metric_key"] == "engagement"
    assert engagement["total"] == 407
    assert engagement["value_available"] is True
    assert engagement["series_available"] is False
    assert engagement["is_available"] is True
    assert engagement["availability"] == "available"
    assert engagement["metric_type"] == "total_value"
    assert engagement["daily_series"] == []
    assert engagement["unavailable_message"] is None
    assert "serie diaria" not in insight_lower
    assert "daily trend" not in insight_lower
    assert "pico" not in insight_lower


def test_instagram_business_metric_without_value_or_series_is_unavailable():
    context = _instagram_context()
    context["report_inputs"]["views"] = None
    context["report_inputs"]["views_daily"] = []
    context["report_inputs"]["normalized_report_metrics"]["views_total"] = None
    context["report_inputs"]["normalized_report_metrics"]["views_daily"] = []
    context["report_inputs"]["unavailable_metrics"] = {"views": "not_returned_by_meta"}

    blocks = build_instagram_business_5_blocks(context)
    views = json.loads(blocks[2]["data_json"])

    assert views["metric_key"] == "views"
    assert views["total"] is None
    assert views["value_available"] is False
    assert views["series_available"] is False
    assert views["is_available"] is False
    assert views["availability"] == "unavailable"
    assert views["unavailable_reason"] == "not_returned_by_meta"
    assert views["unavailable_message"] == "Meta did not return Views for the selected Instagram Business period."


def test_build_instagram_business_5_blocks_engagement_uses_daily_series_when_available():
    context = _instagram_context()
    context["report_inputs"]["daily_engagement"] = [
        {"date": "2026-05-15", "value": 11},
        {"date": "2026-05-16", "value": 9},
    ]
    context["report_inputs"]["engagement"] = None
    context["report_inputs"]["total_interactions"] = None
    context["report_inputs"]["accounts_engaged"] = None
    context["report_inputs"]["content_interactions"] = None
    context["report_inputs"]["normalized_report_metrics"]["total_interactions_total"] = None
    context["report_inputs"]["normalized_report_metrics"]["accounts_engaged_total"] = None
    context["report_inputs"]["normalized_report_metrics"]["interactions_total"] = None
    context["report_inputs"]["normalized_report_metrics"]["engagement_total"] = None
    context["report_inputs"]["normalized_report_metrics"]["content_interactions_total"] = None
    context["engagement"] = None
    blocks = build_instagram_business_5_blocks(context)
    engagement = json.loads(blocks[3]["data_json"])
    assert engagement["metric_key"] == "engagement"
    assert engagement["daily_series"][0]["value"] == 11
    assert engagement["total"] == 20
    assert "insight_full" in engagement


def test_build_5_blocks_summary_uses_page_views_daily_when_available():
    context = _base_context(integration_type="facebook_pages")
    context["page_views"] = None
    context["report_inputs"]["page_views_total"] = None
    context["report_inputs"]["normalized_report_metrics"]["page_views_total"] = None
    context["report_inputs"]["daily_page_views"] = [
        {"date": "2026-05-15", "value": 10},
        {"date": "2026-05-16", "value": 20},
    ]
    context["report_inputs"]["normalized_report_metrics"]["daily_page_views"] = context["report_inputs"]["daily_page_views"]
    blocks = build_5_blocks(context)
    summary = json.loads(blocks[4]["data_json"])
    assert summary["metrics_summary"]["page_views"]["value"] == 30
    assert summary["metrics_summary"]["page_views"]["formatted_value"] == "30"


def test_build_5_blocks_summary_preserves_zero_page_views():
    context = _base_context(integration_type="facebook_pages")
    context["page_views"] = 0
    context["report_inputs"]["page_views_total"] = 0
    context["report_inputs"]["normalized_report_metrics"]["page_views_total"] = 0
    context["report_inputs"]["daily_page_views"] = [
        {"date": "2026-05-15", "value": 0},
        {"date": "2026-05-16", "value": 0},
    ]
    context["report_inputs"]["normalized_report_metrics"]["daily_page_views"] = context["report_inputs"]["daily_page_views"]
    blocks = build_5_blocks(context)
    summary = json.loads(blocks[4]["data_json"])
    assert summary["metrics_summary"]["page_views"]["is_available"] is True
    assert summary["metrics_summary"]["page_views"]["value"] == 0
    assert summary["metrics_summary"]["page_views"]["formatted_value"] == "0"


def test_build_5_blocks_engagement_can_be_calculated_from_components():
    context = _base_context(integration_type="facebook_pages")
    context["engagement"] = None
    context["report_inputs"]["engagement_total"] = None
    context["report_inputs"]["normalized_report_metrics"]["engagement_total"] = None
    context["report_inputs"]["normalized_report_metrics"]["daily_engagement"] = []
    context["report_inputs"]["likes"] = 10
    context["report_inputs"]["comments"] = 5
    context["report_inputs"]["shares"] = 3
    context["report_inputs"]["saves"] = 2
    context["report_inputs"]["reactions"] = 8
    context["report_inputs"]["link_clicks"] = 4
    context["report_inputs"]["daily_engagement"] = []
    context["report_inputs"]["interactions_daily"] = [
        {"date": "2026-05-15", "value": 7},
        {"date": "2026-05-16", "value": 5},
    ]
    blocks = build_5_blocks(context)
    engagement = json.loads(blocks[2]["data_json"])
    assert engagement["total"] is None
    assert engagement["formatted_total"] == "N/A"
    assert engagement["metric_source"] == "not_available"
    assert engagement["daily_series"] == []


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


def test_build_instagram_business_5_blocks_summary_metrics_use_renderable_primitives():
    blocks = build_instagram_business_5_blocks(_instagram_context())
    summary = json.loads(blocks[4]["data_json"])
    metrics_summary = summary["metrics_summary"]
    assert metrics_summary["reach"] == {
        "label": "Reach",
        "value": 9400,
        "formatted_value": "9,400",
        "is_available": True,
        "description": "Total reach",
        "raw_metric_name": "reach",
        "normalized_field": "reach",
        "provider": "instagram_business",
        "availability_status": "available",
        "source_metrics_used": ["reach"],
    }
    assert metrics_summary["engagement"]["value"] == 785
    assert metrics_summary["followers"]["value"] == 11242
    assert metrics_summary["views"]["value"] == 12100
    assert isinstance(metrics_summary["views"]["formatted_value"], str)
    assert not isinstance(metrics_summary["views"]["value"], dict)
    assert "organic_impressions" not in metrics_summary
    assert "page_views" not in metrics_summary
    assert "fans" not in metrics_summary


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

    assert organic_impressions["title"] == "ORGANIC VISIBILITY"
    assert organic_impressions["label"] == "TOTAL ORGANIC IMPRESSIONS"
    assert organic_impressions["metric_key"] == "organic_impressions"
    assert organic_impressions["raw_metric_name"] == "page_posts_impressions_organic"
    assert organic_impressions["formatted_total"] == "546"
    assert organic_impressions["is_available"] is True
    assert organic_impressions["chart"]["metric"] == "organic_impressions"
    assert organic_impressions["chart"]["label"] == "TOTAL ORGANIC IMPRESSIONS"
    assert organic_impressions["daily_series"][0]["date"] == "2026-05-19"

    assert summary["metrics_summary"]["organic_impressions"]["label"] == "Organic Impressions"
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

    assert organic_impressions["title"] == "ORGANIC VISIBILITY"
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


def test_build_5_blocks_facebook_pages_exposes_exact_slide_sequence_without_reach():
    blocks = build_5_blocks(_base_context(integration_type="facebook_pages"))
    slide_types = [json.loads(block["data_json"])["slide_type"] for block in blocks]
    assert slide_types == [
        "cover",
        "organic_impressions_overview",
        "engagement_overview",
        "page_views_overview",
        "executive_summary",
    ]
    assert "reach_overview" not in slide_types
    assert all("reach" not in str(json.loads(block["data_json"]).get("title") or "").lower() for block in blocks[1:4])
    assert all("alcance" not in str(json.loads(block["data_json"]).get("title") or "").lower() for block in blocks[1:4])


def test_facebook_pages_final_report_structure_keeps_executive_summary_as_slide_five():
    blocks = build_5_blocks(_base_context(integration_type="facebook_pages"))
    normalized_blocks = _ensure_facebook_pages_five_slide_structure(blocks)
    slide_payloads = [json.loads(block["data_json"]) for block in normalized_blocks]

    assert [slide["slide_type"] for slide in slide_payloads] == [
        "cover",
        "organic_impressions_overview",
        "engagement_overview",
        "page_views_overview",
        "executive_summary",
    ]
    assert slide_payloads[4]["slide_number"] == 5
    assert slide_payloads[4]["slide_type"] == "executive_summary"
    assert slide_payloads[4]["semantic_name"] == "executive_summary"
    assert slide_payloads[4]["title"] == "Executive Summary"
    assert slide_payloads[4]["slide_type"] != "cover"


def test_facebook_pages_slide_five_includes_top_content_without_adding_slide_six():
    context = _base_context(integration_type="facebook_pages")
    context["report_timeframe"] = {"label": "June 2026", "since": "2026-06-01", "until": "2026-06-30"}
    context["report_inputs"]["top_content"] = [
        {
            "post_id": "post-1",
            "created_time": "2026-06-10T12:00:00+0000",
            "message_preview": "Strong content",
            "permalink_url": "https://facebook.com/post-1",
            "media_type": "photo",
            "impressions": 1000,
            "reach": 800,
            "engaged_users": 90,
            "reactions": 40,
            "comments": 8,
            "shares": 3,
            "engagement_total": 51,
            "score": 90,
        },
        {
            "post_id": "post-2",
            "created_time": "2026-06-12T12:00:00+0000",
            "message_preview": "Fallback content",
            "permalink_url": "https://facebook.com/post-2",
            "media_type": "video",
            "impressions": None,
            "reach": None,
            "engaged_users": None,
            "reactions": 20,
            "comments": 4,
            "shares": 2,
            "engagement_total": 26,
            "score": 26,
        },
    ]

    blocks = build_5_blocks(context)
    slide_payloads = [json.loads(block["data_json"]) for block in blocks]

    assert len(slide_payloads) == 5
    assert slide_payloads[4]["slide_number"] == 5
    assert slide_payloads[4]["slide_type"] == "executive_summary"
    assert slide_payloads[4]["title"] == "Executive Summary"
    assert slide_payloads[4]["top_content_title"] == "Top 5 Content in Selected Period"
    assert [item["post_id"] for item in slide_payloads[4]["top_content"]] == ["post-1", "post-2"]
    assert "page_posts" in slide_payloads[4]["source_metrics_used"]


def test_build_5_blocks_facebook_pages_never_uses_invalid_or_cross_metric_aliases():
    blocks = build_5_blocks(_base_context(integration_type="facebook_pages"))
    slides = [json.loads(block["data_json"]) for block in blocks]
    metric_slides = {slide["metric_key"]: slide for slide in slides if slide.get("metric_key")}

    assert metric_slides["page_views"]["raw_metric_name"] == "page_views_total"
    assert metric_slides["page_views"]["normalized_field"] == "page_views_total"
    assert metric_slides["organic_impressions"]["raw_metric_name"] == "page_posts_impressions_organic"
    assert metric_slides["organic_impressions"]["normalized_field"] == "organic_impressions_total"
    assert metric_slides["page_views"]["raw_metric_name"] not in {
        "page_impressions",
        "page_impressions_unique",
        "page_posts_impressions",
        "page_posts_impressions_unique",
        "page_fans",
    }
    assert metric_slides["organic_impressions"]["raw_metric_name"] not in {
        "page_impressions",
        "page_impressions_unique",
        "page_posts_impressions",
        "page_posts_impressions_unique",
        "page_fans",
    }
    assert metric_slides["page_views"]["raw_metric_name"] != "page_posts_impressions_organic"
    assert metric_slides["organic_impressions"]["raw_metric_name"] != "page_views_total"
