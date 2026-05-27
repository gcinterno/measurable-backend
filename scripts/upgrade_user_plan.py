from __future__ import annotations

import argparse
import json
import sys

from sqlalchemy import inspect

from app.db import SessionLocal
from app.models import AuditLog, Subscription, User, Workspace, WorkspaceMember
from app.services import apply_plan_entitlements, build_default_workspace_name, get_workspace_subscription, normalize_workspace_plan


SUPPORTED_PLANS = ("starter", "pro", "advanced")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Upgrade a user's workspace subscription without Stripe.")
    parser.add_argument("--email", required=True, help="Target user email.")
    parser.add_argument("--plan", default="advanced", choices=SUPPORTED_PLANS, help="Target plan code.")
    parser.add_argument("--workspace-id", type=int, default=None, help="Optional workspace id when the user belongs to multiple workspaces.")
    parser.add_argument(
        "--reason",
        default="Manual plan upgrade for review/testing",
        help="Reason stored in audit metadata.",
    )
    return parser.parse_args()


def resolve_workspace_for_user(db, *, user_id: int, workspace_id: int | None) -> Workspace:
    memberships = (
        db.query(Workspace, WorkspaceMember.role)
        .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
        .filter(WorkspaceMember.user_id == user_id)
        .order_by(Workspace.created_at.asc(), Workspace.id.asc())
        .all()
    )
    if workspace_id is not None:
        for workspace, _role in memberships:
            if workspace.id == workspace_id:
                return workspace
        raise ValueError(f"workspace_id={workspace_id} does not belong to the user.")

    if not memberships:
        raise ValueError("User does not belong to any workspace.")
    if len(memberships) > 1:
        workspace_ids = [workspace.id for workspace, _role in memberships]
        raise ValueError(
            "User belongs to multiple workspaces. Re-run with --workspace-id. "
            f"Available workspace ids: {workspace_ids}"
        )
    return memberships[0][0]


def ensure_workspace_subscription(db, *, user: User, workspace: Workspace) -> Subscription:
    subscription = get_workspace_subscription(db, workspace.id)
    if subscription is not None:
        apply_plan_entitlements(subscription, subscription.plan or "free")
        return subscription

    subscription = Subscription(
        workspace_id=workspace.id,
        plan="free",
        status="active",
        billing_status="free",
    )
    apply_plan_entitlements(subscription, "free")
    db.add(subscription)
    db.flush()
    return subscription


def create_workspace_for_user(db, *, user: User) -> Workspace:
    workspace = Workspace(name=build_default_workspace_name(user.full_name))
    db.add(workspace)
    db.flush()
    db.add(WorkspaceMember(workspace_id=workspace.id, user_id=user.id, role="owner"))
    db.flush()
    return workspace


def maybe_write_audit_log(db, *, workspace_id: int, user_id: int, previous_plan: str, new_plan: str, reason: str) -> bool:
    bind = db.get_bind()
    table_names = set(inspect(bind).get_table_names())
    if "audit_logs" not in table_names:
        return False
    audit_log = AuditLog(
        workspace_id=workspace_id,
        user_id=user_id,
        action="manual_plan_upgrade",
        metadata_json=json.dumps(
            {
                "reason": reason,
                "previous_plan": previous_plan,
                "new_plan": new_plan,
            }
        ),
    )
    db.add(audit_log)
    return True


def main() -> int:
    args = parse_args()
    normalized_email = args.email.strip().lower()
    target_plan = normalize_workspace_plan(args.plan)

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == normalized_email).one_or_none()
        if user is None:
            print(f"ERROR: user not found for email={normalized_email}")
            return 1

        try:
            workspace = resolve_workspace_for_user(db, user_id=user.id, workspace_id=args.workspace_id)
        except ValueError as exc:
            memberships_exist = (
                db.query(WorkspaceMember).filter(WorkspaceMember.user_id == user.id).count() > 0
            )
            if not memberships_exist and args.workspace_id is None:
                workspace = create_workspace_for_user(db, user=user)
            else:
                print(f"ERROR: {exc}")
                return 1

        subscription = ensure_workspace_subscription(db, user=user, workspace=workspace)
        previous_plan = normalize_workspace_plan(subscription.plan or "free")

        apply_plan_entitlements(subscription, target_plan)
        subscription.status = "active"
        subscription.billing_status = "active"
        subscription.cancel_at_period_end = False
        db.add(subscription)

        audit_logged = maybe_write_audit_log(
            db,
            workspace_id=workspace.id,
            user_id=user.id,
            previous_plan=previous_plan,
            new_plan=target_plan,
            reason=args.reason,
        )

        db.commit()
        db.refresh(subscription)

        print(
            json.dumps(
                {
                    "user_id": user.id,
                    "email": user.email,
                    "workspace_id": workspace.id,
                    "previous_plan": previous_plan,
                    "new_plan": subscription.plan,
                    "subscription_status": subscription.status,
                    "billing_status": subscription.billing_status,
                    "audit_logged": audit_logged,
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return 0
    except Exception as exc:
        db.rollback()
        print(f"ERROR: failed to upgrade plan for {normalized_email}: {exc.__class__.__name__}: {exc}")
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
