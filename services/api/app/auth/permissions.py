"""Roles and permissions: the single source for what each role may do.

Two separate questions are answered here (docs/security/permissions.md):

* **System permissions** follow the account role. Administrators manage accounts, connector
  credentials, notification destinations and case access; they can read the system audit trail.
  Administration grants no access to case content: an administrator opens a case only through a
  case membership, like everyone else.
* **Case permissions** follow the *effective* case role, which is the membership role capped by
  the account role. Analysts work on a case; viewers read it. Viewers may open every record of a
  case they belong to, including single evidence downloads and keyword search of indexed text, but
  may not start model-backed AI requests, build bulk exports or reports, collect, import, or change
  anything.
"""

from __future__ import annotations

import enum

from app.auth.models import AccountRole
from app.cases.models import CaseRole


class SystemPermission(enum.StrEnum):
    CREATE_CASE = "cases.create"
    SEARCH_ACCOUNTS = "accounts.search"
    MANAGE_ACCOUNTS = "accounts.manage"
    MANAGE_CASE_ACCESS = "case_access.manage"
    MANAGE_CREDENTIALS = "credentials.manage"
    MANAGE_NOTIFICATION_DESTINATIONS = "notification_destinations.manage"
    READ_SYSTEM_AUDIT = "audit.system.read"


class CasePermission(enum.StrEnum):
    READ = "case.read"
    EDIT = "case.edit"
    MANAGE_MEMBERS = "case.members.manage"
    IMPORT = "evidence.import"
    RUN_QUERIES = "queries.run"
    MANAGE_MONITORS = "monitors.manage"
    REQUEST_AI = "ai.request"
    EXPORT = "exports.create"
    MANAGE_RETENTION = "retention.manage"
    DELETE_CASE = "case.delete"
    READ_AUDIT = "audit.case.read"


SYSTEM_PERMISSIONS: dict[AccountRole, frozenset[SystemPermission]] = {
    AccountRole.ADMINISTRATOR: frozenset(SystemPermission),
    AccountRole.ANALYST: frozenset(
        {SystemPermission.CREATE_CASE, SystemPermission.SEARCH_ACCOUNTS}
    ),
    AccountRole.VIEWER: frozenset(),
}

CASE_PERMISSIONS: dict[CaseRole, frozenset[CasePermission]] = {
    CaseRole.ANALYST: frozenset(CasePermission),
    CaseRole.VIEWER: frozenset({CasePermission.READ}),
}

# Account roles that may hold each membership role.
ALLOWED_MEMBERSHIP_ROLES: dict[AccountRole, frozenset[CaseRole]] = {
    AccountRole.ADMINISTRATOR: frozenset(CaseRole),
    AccountRole.ANALYST: frozenset(CaseRole),
    AccountRole.VIEWER: frozenset({CaseRole.VIEWER}),
}


def account_role(value: str) -> AccountRole:
    try:
        return AccountRole(value)
    except ValueError:
        # An unknown stored value never grants anything.
        return AccountRole.VIEWER


def effective_case_role(account: str, membership: str) -> CaseRole:
    """The membership role capped by the account role."""
    if account_role(account) == AccountRole.VIEWER:
        return CaseRole.VIEWER
    try:
        return CaseRole(membership)
    except ValueError:
        return CaseRole.VIEWER


def has_system_permission(account: str, permission: SystemPermission) -> bool:
    return permission in SYSTEM_PERMISSIONS[account_role(account)]


def case_permissions(role: CaseRole) -> frozenset[CasePermission]:
    return CASE_PERMISSIONS[role]


def system_permissions(account: str) -> frozenset[SystemPermission]:
    return SYSTEM_PERMISSIONS[account_role(account)]
