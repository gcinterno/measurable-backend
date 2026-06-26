from __future__ import annotations

import json
import os

os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/measurable_ten_slide_test.db?check_same_thread=false")
os.environ.setdefault("JWT_SECRET", "test-jwt-secret")

from app.main import build_10_blocks


def _context() -> dict:
    return {
        "title": "Executive report",
        "plan": "core",
        "page_name": "Acme Account",
        "followers": 1200,
        "reach": 2400,
        "engagement": 180,
        "impressions": 3600,
        "branding": {},
        "report_timeframe": {
            "label": "Last 28 days",
            "since": "2026-05-01",
            "until": "2026-05-28",
        },
        "report_inputs": {
            "integration_type": "facebook_pages",
            "reach": 2400,
            "impressions": 3600,
            "engagement": 180,
            "followers": 1200,
            "profile_visits": 300,
            "page_views": 300,
            "reach_daily": [
                {"date": "2026-05-15", "value": 1200},
                {"date": "2026-05-16", "value": 1200},
            ],
            "impressions_daily": [
                {"date": "2026-05-15", "value": 1800},
                {"date": "2026-05-16", "value": 1800},
            ],
            "daily_engagement": [
                {"date": "2026-05-15", "value": 90},
                {"date": "2026-05-16", "value": 90},
            ],
            "page_visits_daily": [
                {"date": "2026-05-15", "value": 150},
                {"date": "2026-05-16", "value": 150},
            ],
            "recent_posts": [
                {
                    "id": "post-1",
                    "message": "Top post creative",
                    "created_time": "2026-05-15",
                    "reach": 900,
                    "reactions": 30,
                    "comments": 10,
                    "shares": 4,
                },
                {
                    "id": "post-2",
                    "message": "Second post creative",
                    "created_time": "2026-05-16",
                    "reach": 800,
                    "reactions": 20,
                    "comments": 8,
                    "shares": 3,
                },
            ],
            "previous_period": {
                "previous_reach": 2000,
                "previous_impressions": 3000,
                "previous_engagement": 150,
                "previous_page_views": 250,
                "previous_followers": 1100,
            },
        },
        "reach_chart_data": {
            "metric": "reach",
            "points": [
                {"date": "2026-05-15", "label": "May 15", "value": 1200},
                {"date": "2026-05-16", "label": "May 16", "value": 1200},
            ],
            "timeframe": {"label": "Last 28 days"},
        },
        "impressions_slide_payload": {
            "impressions_daily": [
                {"date": "2026-05-15", "value": 1800},
                {"date": "2026-05-16", "value": 1800},
            ]
        },
    }


def _payloads(blocks: list[dict]) -> list[dict]:
    return [json.loads(block["data_json"]) for block in blocks]


def test_build_10_blocks_generates_exact_semantic_order_and_preserves_cover():
    payloads = _payloads(build_10_blocks(_context()))

    assert [payload["semantic_name"] for payload in payloads] == [
        "cover",
        "reach",
        "impressions",
        "engagement",
        "page_visits",
        "audience_growth",
        "content_activity",
        "top_performing_content",
        "executive_insights",
        "recommendations",
    ]
    assert payloads[0]["semantic_name"] == "cover"
    assert payloads[0]["subtitle"] == "Acme Account performance report · Last 28 days"


def test_build_10_blocks_adds_growth_metadata_and_marks_missing_previous_as_na():
    payloads = _payloads(build_10_blocks(_context()))

    reach = payloads[1]
    assert reach["growth"]["previous_value"] == 2000.0
    assert reach["growth_percent"] == 20.0
    assert reach["growth_label"] == "+20%"

    content_activity = payloads[6]
    assert content_activity["growth_percent"] is None
    assert content_activity["growth_label"] == "N/A"
