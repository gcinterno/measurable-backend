from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

TEST_DB_PATH = Path("/tmp/measurable_report_recipe_catalog_api.db")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DB_PATH}?check_same_thread=false")
os.environ.setdefault("JWT_SECRET", "test-jwt-secret")
os.environ.setdefault("AWS_REGION", "us-east-1")
os.environ.setdefault("S3_INPUTS_BUCKET", "test-inputs")
os.environ.setdefault("S3_OUTPUTS_BUCKET", "test-outputs")
os.environ.setdefault("EXPORT_LAMBDA_URL", "https://example.com/export")
os.environ.setdefault("SES_FROM_EMAIL", "no-reply@measurable.test")
os.environ.setdefault("FRONTEND_BASE_URL", "http://localhost:3000")

from app.deps import get_current_user
from app.main import app
from app.models import User


@pytest.fixture()
def client():
    app.dependency_overrides[get_current_user] = lambda: User(
        id=1,
        email="recipe-reader@example.com",
        password_hash="unused",
        email_verified=True,
        is_active=True,
    )
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _assert_facebook_pages_5_recipe(payload: dict) -> None:
    assert payload["id"] == "facebook_pages_5"
    assert payload["platform"] == "facebook_pages"
    assert payload["name"] == "Facebook Pages · 5 Slides"
    assert payload["version"] == 1
    assert payload["slide_count"] == 5
    assert payload["slides"] == [
        {"order": 1, "semantic_name": "cover"},
        {"order": 2, "semantic_name": "organic_impressions_overview"},
        {"order": 3, "semantic_name": "engagement_overview"},
        {"order": 4, "semantic_name": "page_views_overview"},
        {"order": 5, "semantic_name": "executive_summary"},
    ]


def test_report_recipe_catalog_lists_registered_recipes(client: TestClient) -> None:
    response = client.get("/report-recipes")

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    _assert_facebook_pages_5_recipe(payload[0])


def test_report_recipe_catalog_supports_platform_filter(client: TestClient) -> None:
    response = client.get("/report-recipes", params={"platform": "facebook_pages"})

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    _assert_facebook_pages_5_recipe(payload[0])

    empty_response = client.get("/report-recipes", params={"platform": "instagram_business"})
    assert empty_response.status_code == 200
    assert empty_response.json() == []


def test_report_recipe_catalog_returns_existing_recipe(client: TestClient) -> None:
    response = client.get("/report-recipes/facebook_pages_5")

    assert response.status_code == 200
    _assert_facebook_pages_5_recipe(response.json())


def test_report_recipe_catalog_returns_404_for_unknown_recipe(client: TestClient) -> None:
    response = client.get("/report-recipes/unknown")

    assert response.status_code == 404
    assert response.json() == {
        "detail": {
            "code": "recipe_not_found",
            "message": "Report recipe not found.",
        }
    }


def test_report_recipe_catalog_requires_authentication() -> None:
    with TestClient(app) as test_client:
        response = test_client.get("/report-recipes")

    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "missing_token"
