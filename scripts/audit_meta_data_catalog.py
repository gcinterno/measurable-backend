from __future__ import annotations

import argparse
import json

from app.db import SessionLocal
from app.meta_data_catalog import run_meta_data_catalog_audit


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit Meta data catalog by provider.")
    parser.add_argument("--workspace-id", type=int, required=True)
    parser.add_argument(
        "--providers",
        nargs="*",
        default=["facebook_pages", "instagram_business", "meta_ads"],
    )
    parser.add_argument("--output-dir", default="tmp")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        result = run_meta_data_catalog_audit(
            db,
            workspace_id=args.workspace_id,
            providers=list(args.providers or []),
            output_dir=args.output_dir,
        )
    finally:
        db.close()

    print(
        json.dumps(
            {
                "workspace_id": result["workspace_id"],
                "json_path": result["json_path"],
                "csv_path": result["csv_path"],
                "summary": result["summary"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
