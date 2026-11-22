"""Add platform-scoped RBAC roles, permissions, assignments and audit trail.

Revision ID: 0012_platform_rbac
Revises: 0011_chat_analysis_review
"""

import os
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision = "0012_platform_rbac"
down_revision = "0011_chat_analysis_review"
branch_labels = None
depends_on = None


ROLE_DEFINITIONS = (
    ("super_admin", "超级管理员", "拥有全部平台管理权限"),
    ("platform_operator", "平台运营", "管理平台用户、租户及运营配置"),
    ("finance", "财务管理员", "管理套餐、订阅、计费及财务数据"),
    ("support", "客户支持", "查看客户及租户基础信息，不默认访问客户内容"),
    ("read_only", "平台只读", "只读查看已授权的平台运营信息"),
)

PERMISSIONS = (
    ("platform.admin", "完整平台管理权限"),
    ("platform.access", "进入平台管理区域"),
    ("platform.rbac.manage", "管理平台角色及用户授权"),
    ("platform.users.read", "查看平台用户"),
    ("platform.users.manage", "管理用户账户状态"),
    ("platform.tenants.read", "查看平台租户"),
    ("platform.tenants.manage", "管理租户状态"),
    ("platform.models.read", "查看平台模型配置"),
    ("platform.models.manage", "管理平台模型及价格"),
    ("platform.billing.read", "查看套餐、订阅及账本"),
    ("platform.billing.manage", "管理套餐、订阅及计费调整"),
    ("platform.analytics.read", "查看平台运营统计"),
    ("platform.operations.read", "查看系统及 Worker 运行状态"),
    ("platform.audit.read", "查看平台管理审计记录"),
)

ROLE_PERMISSION_CODES = {
    "super_admin": {code for code, _ in PERMISSIONS},
    "platform_operator": {
        "platform.access", "platform.users.read", "platform.users.manage",
        "platform.tenants.read", "platform.tenants.manage",
        "platform.models.read", "platform.analytics.read",
        "platform.operations.read", "platform.audit.read",
    },
    "finance": {
        "platform.access", "platform.billing.read", "platform.billing.manage",
        "platform.analytics.read", "platform.audit.read",
    },
    "support": {
        "platform.access", "platform.users.read", "platform.tenants.read",
        "platform.audit.read",
    },
    "read_only": {
        "platform.access", "platform.users.read", "platform.tenants.read",
        "platform.models.read", "platform.billing.read",
        "platform.analytics.read", "platform.operations.read", "platform.audit.read",
    },
}


def upgrade() -> None:
    op.create_table(
        "platform_roles",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("code", sa.String(64), nullable=False, unique=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("is_system", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        )
    )
    op.create_table(
        "platform_permissions",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("code", sa.String(100), nullable=False, unique=True),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
    )
    op.create_table(
        "platform_role_permissions",
        sa.Column(
            "role_id", sa.Uuid(as_uuid=True),
            sa.ForeignKey("platform_roles.id", ondelete="CASCADE"),
            primary_key=True, nullable=False,
        ),
        sa.Column(
            "permission_id", sa.Uuid(as_uuid=True),
            sa.ForeignKey("platform_permissions.id", ondelete="CASCADE"),
            primary_key=True, nullable=False,
        ),
    )
    op.create_table(
        "platform_user_roles",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "user_id", sa.Uuid(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "role_id", sa.Uuid(as_uuid=True),
            sa.ForeignKey("platform_roles.id", ondelete="RESTRICT"), nullable=False,
        ),
        sa.Column(
            "granted_by_user_id", sa.Uuid(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        )
    )
    op.create_index(
        "ix_platform_user_roles_user_active", "platform_user_roles",
        ["user_id", "revoked_at", "expires_at"],
    )
    op.create_index(
        "ix_platform_user_roles_role_active", "platform_user_roles",
        ["role_id", "revoked_at", "expires_at"],
    )
    op.create_table(
        "platform_admin_audit_logs",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "actor_user_id", sa.Uuid(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        )
        sa.Column(
            "target_user_id", sa.Uuid(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        )
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("resource_type", sa.String(80), nullable=False),
        sa.Column("resource_id", sa.String(160), nullable=False),
        sa.Column("result", sa.String(20), nullable=False, server_default="success"),
        sa.Column("reason", sa.String(500)),
        sa.Column("request_id", sa.String(100)),
        sa.Column(
            "details", sa.JSON(), nullable=False, server_default=sa.text("'{}'"),
        )
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        )
    )
    op.create_index("ix_platform_admin_audit_created", "platform_admin_audit_logs", ["created_at"])
    op.create_index(
        "ix_platform_admin_audit_actor_created", "platform_admin_audit_logs",
        ["actor_user_id", "created_at"],
    )
    op.create_index(
        "ix_platform_admin_audit_target_created", "platform_admin_audit_logs",
        ["target_user_id", "created_at"],
    )

    roles_table = sa.table(
        "platform_roles",
        sa.column("id", sa.Uuid(as_uuid=True)),
        sa.column("code", sa.String()),
        sa.column("name", sa.String()),
        sa.column("description", sa.Text()),
        sa.column("is_system", sa.Boolean()),
    )
    permissions_table = sa.table(
        "platform_permissions",
        sa.column("id", sa.Uuid(as_uuid=True)),
        sa.column("code", sa.String()),
        sa.column("description", sa.Text()),
    )
    role_permissions_table = sa.table(
        "platform_role_permissions",
        sa.column("role_id", sa.Uuid(as_uuid=True)),
        sa.column("permission_id", sa.Uuid(as_uuid=True)),
    )

    role_ids = {code: uuid4() for code, _, _ in ROLE_DEFINITIONS}
    permission_ids = {code: uuid4() for code, _ in PERMISSIONS}
    op.bulk_insert(
        roles_table,
        [
            {
                "id": role_ids[code], "code": code, "name": name,
                "description": description, "is_system": True,
            }
            for code, name, description in ROLE_DEFINITIONS
        ],
    )
    op.bulk_insert(
        permissions_table,
        [
            {"id": permission_ids[code], "code": code, "description": description}
            for code, description in PERMISSIONS
        ],
    )
    op.bulk_insert(
        role_permissions_table,
        [
            {"role_id": role_ids[role_code], "permission_id": permission_ids[permission_code]}
            for role_code, permission_codes in ROLE_PERMISSION_CODES.items()
            for permission_code in sorted(permission_codes)
        ],
    )

    # Bootstrap only accounts that already exist at migration time. Public
    # registration is separately blocked from claiming reserved admin emails.
    configured_emails = {
        value.strip().lower()
        for value in os.getenv("SUPERNOVA_PLATFORM_ADMIN_EMAILS", "").split(",")
        if value.strip()
    }
    if configured_emails:
        bind = op.get_bind()
        users = sa.table(
            "users",
            sa.column("id", sa.Uuid(as_uuid=True)),
            sa.column("email", sa.String()),
        )
        assignments = sa.table(
            "platform_user_roles",
            sa.column("id", sa.Uuid(as_uuid=True)),
            sa.column("user_id", sa.Uuid(as_uuid=True)),
            sa.column("role_id", sa.Uuid(as_uuid=True)),
            sa.column("granted_by_user_id", sa.Uuid(as_uuid=True)),
        )
        existing_users = bind.execute(
            sa.select(users.c.id).where(sa.func.lower(users.c.email).in_(configured_emails))
        ).scalars().all()
        for user_id in existing_users:
            bind.execute(
                assignments.insert().values(
                    id=uuid4(),
                    user_id=user_id,
                    role_id=role_ids["super_admin"],
                    granted_by_user_id=None,
                )
            )
            audit_table = sa.table(
                "platform_admin_audit_logs",
                sa.column("id", sa.Uuid(as_uuid=True)),
                sa.column("actor_user_id", sa.Uuid(as_uuid=True)),
                sa.column("target_user_id", sa.Uuid(as_uuid=True)),
                sa.column("action", sa.String()),
                sa.column("resource_type", sa.String()),
                sa.column("resource_id", sa.String()),
                sa.column("result", sa.String()),
                sa.column("reason", sa.String()),
                sa.column("details", sa.JSON()),
            )
            bind.execute(
                audit_table.insert().values(
                    id=uuid4(),
                    actor_user_id=None,
                    target_user_id=user_id,
                    action="platform.role.bootstrap",
                    resource_type="user",
                    resource_id=str(user_id),
                    result="success",
                    reason="Initial migration from SUPERNOVA_PLATFORM_ADMIN_EMAILS",
                    details={"source": "environment_allowlist"},
                )
            )


def downgrade() -> None:
    op.drop_index("ix_platform_admin_audit_target_created", table_name="platform_admin_audit_logs")
    op.drop_index("ix_platform_admin_audit_actor_created", table_name="platform_admin_audit_logs")
    op.drop_index("ix_platform_admin_audit_created", table_name="platform_admin_audit_logs")
    op.drop_table("platform_admin_audit_logs")
    op.drop_index("ix_platform_user_roles_role_active", table_name="platform_user_roles")
    op.drop_index("ix_platform_user_roles_user_active", table_name="platform_user_roles")
    op.drop_table("platform_user_roles")
    op.drop_table("platform_role_permissions")
    op.drop_table("platform_permissions")
    op.drop_table("platform_roles")
