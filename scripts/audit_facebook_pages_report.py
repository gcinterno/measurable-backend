from __future__ import annotations

import argparse
import json

from app.db import SessionLocal
from app.models import Dataset, Report, ReportBlock, ReportVersion


def _parse_block_data(value: str | None) -> dict:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {"_raw": value}
    return parsed if isinstance(parsed, dict) else {"_raw": parsed}


def _metric_entry(
    *,
    payload: dict,
    audit: dict,
    metric_key: str,
    normalized_field: str,
    daily_field: str | None = None,
) -> dict:
    daily_series = payload.get(daily_field) if daily_field else None
    return {
        "total": payload.get(metric_key) if metric_key in payload else None,
        "source": (audit.get(metric_key.replace("_total", "").replace("_daily", "")) or {}).get("source_metric"),
        "points_count": len(daily_series) if isinstance(daily_series, list) else 0,
        "raw_values": daily_series if isinstance(daily_series, list) else [],
        "unavailable_reason": (audit.get(metric_key.replace("_total", "").replace("_daily", "")) or {}).get("unavailable_reason"),
        "normalized_field": normalized_field,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("report_id", type=int)
    args = parser.parse_args()

    session = SessionLocal()
    try:
        report = session.get(Report, args.report_id)
        if not report:
            print(json.dumps({"error": "report_not_found", "report_id": args.report_id}, indent=2))
            return

        version = (
            session.query(ReportVersion)
            .filter(ReportVersion.report_id == report.id)
            .order_by(ReportVersion.version.desc(), ReportVersion.id.desc())
            .first()
        )
        dataset = session.get(Dataset, report.dataset_id)
        dataset_data = dataset.data if dataset and isinstance(dataset.data, dict) else {}
        timeframe = dataset_data.get("timeframe") if isinstance(dataset_data.get("timeframe"), dict) else {}
        audit = dataset_data.get("facebook_metric_audit") if isinstance(dataset_data.get("facebook_metric_audit"), dict) else {}

        blocks = (
            session.query(ReportBlock)
            .filter(ReportBlock.report_version_id == version.id)
            .order_by(ReportBlock.order.asc(), ReportBlock.id.asc())
            .all()
            if version
            else []
        )
        block_mapping = {}
        for block in blocks:
            data = _parse_block_data(block.data_json)
            if block.order in {2, 3, 4, 5}:
                block_mapping[f"slide_{block.order:02d}"] = {
                    "semantic_name": data.get("semantic_name"),
                    "title": data.get("title"),
                    "metric_key": data.get("metric_key"),
                    "block_id": block.id,
                }

        output = {
            "report_id": report.id,
            "dataset_id": dataset.id if dataset else None,
            "page_name": dataset_data.get("page_name"),
            "period": timeframe.get("label"),
            "metrics": {
                "reach": {
                    "total": dataset_data.get("reach_total"),
                    "source": (audit.get("reach") or {}).get("source_metric"),
                    "points_count": len(dataset_data.get("daily_reach") or []) if isinstance(dataset_data.get("daily_reach"), list) else 0,
                    "raw_values": dataset_data.get("daily_reach") if isinstance(dataset_data.get("daily_reach"), list) else [],
                    "unavailable_reason": (audit.get("reach") or {}).get("unavailable_reason"),
                },
                "impressions": {
                    "total": dataset_data.get("impressions_total"),
                    "source": (audit.get("impressions") or {}).get("source_metric"),
                    "points_count": len(dataset_data.get("daily_impressions") or []) if isinstance(dataset_data.get("daily_impressions"), list) else 0,
                    "raw_values": dataset_data.get("daily_impressions") if isinstance(dataset_data.get("daily_impressions"), list) else [],
                    "unavailable_reason": (audit.get("impressions") or {}).get("unavailable_reason"),
                },
                "engagement": {
                    "total": dataset_data.get("engagement_total"),
                    "source": (audit.get("engagement") or {}).get("source_metric"),
                    "points_count": len(dataset_data.get("daily_engagement") or []) if isinstance(dataset_data.get("daily_engagement"), list) else 0,
                    "raw_values": dataset_data.get("daily_engagement") if isinstance(dataset_data.get("daily_engagement"), list) else [],
                    "unavailable_reason": (audit.get("engagement") or {}).get("unavailable_reason"),
                },
                "page_views": {
                    "total": dataset_data.get("page_views_total"),
                    "source": (audit.get("page_views") or {}).get("source_metric"),
                    "points_count": len(dataset_data.get("daily_page_views") or []) if isinstance(dataset_data.get("daily_page_views"), list) else 0,
                    "raw_values": dataset_data.get("daily_page_views") if isinstance(dataset_data.get("daily_page_views"), list) else [],
                    "unavailable_reason": (audit.get("page_views") or {}).get("unavailable_reason"),
                },
                "followers": {
                    "total": dataset_data.get("followers_total"),
                    "source": (audit.get("followers") or {}).get("source_metric"),
                    "unavailable_reason": (audit.get("followers") or {}).get("unavailable_reason"),
                },
            },
            "block_mapping": block_mapping,
        }
        print(json.dumps(output, indent=2, ensure_ascii=False))
    finally:
        session.close()


if __name__ == "__main__":
    main()
