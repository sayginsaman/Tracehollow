# ADR 0010: Team roles, case membership and administration without case access

- Status: accepted
- Date: 2026-09-17
- Phase: 5

## Context

Phases 0–4 had one flag, `users.is_admin`, and case membership with a single `owner` role. Phase 5
requires administrator, analyst and viewer roles, case-level membership, API-level enforcement
including AI and export routes and background work, membership screens on the existing local
account model (no email invitations, SSO or new identity provider), a safe migration and lockout
protection. The previous model did not give administrators access to case content, and the phase
brief forbids granting it silently.

## Decision

1. **Two role axes.** `users.role` is the account role (`administrator`, `analyst`, `viewer`);
   `case_members.role` is the membership role (`analyst`, `viewer`). The effective case role is the
   membership role capped by the account role, so a viewer account can never act as an analyst.
2. **Administration is separate from case access.** Administrators hold system permissions
   (accounts, case access, credentials, notification destinations, system audit) and no case
   permissions. Case routes resolve access only through membership and answer 404 to non-members,
   administrators included. Administrators repair access through a directory that shows titles and
   member counts only; adding themselves is an explicit, audited membership.
3. **One permission module.** `app/auth/permissions.py` maps roles to named permissions; route
   dependencies (`ReadableCase`, `AnalystCase`, `WritableCase`, `require_system_permission`) use
   it, `GET /auth/session` and case details return the permission names so the interface can hide
   unavailable actions, and refusals are 403 with `insufficient_case_role` or
   `insufficient_account_role` plus a denied audit event.
4. **Viewer policy.** Viewers read everything in their cases, including single evidence downloads
   and keyword search of the index. They cannot start model-backed AI requests (model time,
   possible cloud processing) or create bulk exports and reports (moving a case out in one step),
   and cannot change, collect, import or delete.
5. **Background re-checks.** Executions, AI requests and processing jobs re-check the requester's
   analyst access when claimed and before each unit of work and stop with
   `authorization_revoked`; monitors re-check the analyst who enabled them at dispatch; webhook
   deliveries re-check the subscriber; notification lists filter by current membership.
6. **Lockout protection.** The last active administrator cannot be demoted or deactivated
   (serialized with a row lock); a case's last active analyst cannot be removed or demoted by
   members; account changes that orphan cases are allowed but reported, and administrators can add
   analysts through case access. The host CLI password reset remains.
7. **Migration 0006** sets `role` from `is_admin` (kept as a compatibility property), turns every
   `owner` membership into `analyst`, adds `added_by_user_id`/`updated_at` to memberships and
   creates `audit_events` (ADR 0014). It is reversible.

## Consequences

- Existing installations keep working: former administrators are administrators, everyone else is
  an analyst account, and case owners are analysts of their cases.
- An administrator who needs to see a case must become a member, which is visible to the case's
  analysts and in the audit trail.
- Viewers who need an export ask an analyst. Single evidence files remain downloadable.
- The permission matrix is documented in `docs/security/permissions.md` and tested route by route
  in `tests/test_team_access.py`.
