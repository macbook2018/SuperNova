# Platform RBAC

## Scope

Platform authorization is independent from tenant membership authorization.

- JWT claims remain `sub` and `tid`; platform roles are never trusted from token claims.
- `TenantMembership.role` continues to represent workspace-level roles only.
- Every authenticated request resolves active platform roles and permissions from PostgreSQL.
- Platform role changes take effect on the next request; no token refresh is required.
- Tenant-scoped resources continue to use the authenticated tenant context and existing tenant isolation rules.

## System roles

| Role | Purpose | Initial permission scope |
| --- | --- | --- |
| `super_admin` | Full platform administration | All platform permissions |
| `platform_operator` | Platform operations | Users, tenants, model read, analytics, operations and audit read |
| `finance` | Finance operations | Billing read/write, analytics and audit read |
| `support` | Customer support | User and tenant basic read, audit read |
| `read_only` | Platform observer | Read-only user, tenant, model, billing, analytics, operations and audit |

System roles are seeded by migration `0012_platform_rbac`. Role-to-permission mappings are stored in `platform_role_permissions`.

## Authorization tables

- `platform_roles`
- `platform_permissions`
- `platform_role_permissions`
- `platform_user_roles`
- `platform_admin_audit_logs`

Platform assignments support expiration and revocation. Tenant membership rows are not modified by platform role operations.

## Initial administrator bootstrap

At migration time, existing user accounts matching `SUPERNOVA_PLATFORM_ADMIN_EMAILS` receive `super_admin`. The migration records each bootstrap grant in the platform audit log.

Public registration rejects addresses in `SUPERNOVA_PLATFORM_ADMIN_EMAILS`, preventing a new registrant from claiming an unregistered reserved administrator address.

After migration, database role assignments are authoritative. Adding an email to the environment variable later does not automatically grant a role; an existing Super Admin must use the platform role administration API.

Before applying migration `0012_platform_rbac` in Staging, verify that:
1. `SUPERNOVA_PLATFORM_ADMIN_EMAILS` contains the intended existing administrator account email.
2. That account already exists in the `users` table.
3. The same environment variable is available to the API container when Alembic runs.

## API

All endpoints require the existing bearer-token authentication.

| Method | Endpoint | Permission |
| --- | --- | --- |
| GET | `/admin/platform/access` | Authenticated user; returns own platform permissions |
| GET | `/admin/platform/roles` | `platform.rbac.manage` |
| GET | `/admin/platform/users/{user_id}/roles` | `platform.rbac.manage` |
| POST | `/admin/platform/users/{user_id}/roles/{role_code}` | `platform.rbac.manage` |
| DELETE | `/admin/platform/users/{user_id}/roles/{role_code}` | `platform.rbac.manage` |
| GET | `/admin/platform/audit-logs` | `platform.audit.read` |

Role grants and revocations require a reason. Expiry timestamps must include a timezone. The API prevents revoking the last active Super Admin.

## Auditing

Platform audit events are append-only at the application API layer. The current audit coverage includes:

- Platform role grants and revocations
- Platform model create/update and price-version creation
- Platform Embedding model create/update
- Billing plan creation
- Tenant subscription assignment
- Billing reconciliation runs

Audit entries include actor, target, action, resource, reason, request ID, timestamp and structured details. Secret values are not written to the audit log.

## Current permission enforcement

- Platform model administration: `platform.models.read` / `platform.models.manage`
- Platform Embedding administration: `platform.models.read` / `platform.models.manage`
- Billing administration: `platform.billing.read` / `platform.billing.manage`
- Role administration: `platform.rbac.manage`
- Audit history: `platform.audit.read`

The Console's existing Platform Model menu is displayed only when the current user has model read or manage permission. Backend authorization remains authoritative.
