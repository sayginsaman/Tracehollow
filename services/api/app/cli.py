"""Operator commands: ``python -m app.cli <command>``.

These run inside the API container and therefore require local shell access, which is
why they do not ask for the web setup token.
"""

from __future__ import annotations

import argparse
import getpass
import sys

from app.auth import service
from app.config import ConfigurationError, get_settings
from app.db.session import create_db_engine, create_session_factory, session_scope


def _read_password(from_stdin: bool) -> str:
    if from_stdin:
        return sys.stdin.readline().rstrip("\r\n")
    first = getpass.getpass("New password: ")
    if first != getpass.getpass("Repeat password: "):
        raise SystemExit("Passwords do not match.")
    return first


def _cmd_check_config(_: argparse.Namespace) -> int:
    settings = get_settings()
    print(f"Configuration valid (environment={settings.env}).")
    return 0


def _cmd_create_admin(args: argparse.Namespace) -> int:
    factory = create_session_factory(create_db_engine(get_settings()))
    password = _read_password(args.password_stdin)
    try:
        with session_scope(factory) as db:
            user = service.create_initial_admin(db, username=args.username, password=password)
            username = user.username
    except service.SetupAlreadyCompletedError:
        print("An administrator already exists; nothing was changed.", file=sys.stderr)
        return 1
    except service.UsernameTakenError:
        print("That username is already taken; nothing was changed.", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"Rejected: {exc}", file=sys.stderr)
        return 1
    print(f"Administrator '{username}' created.")
    return 0


def _cmd_reset_password(args: argparse.Namespace) -> int:
    factory = create_session_factory(create_db_engine(get_settings()))
    password = _read_password(args.password_stdin)
    try:
        with session_scope(factory) as db:
            user = service.reset_password(db, username=args.username, new_password=password)
            if user is None:
                print("No such user; nothing was changed.", file=sys.stderr)
                return 1
    except ValueError as exc:
        print(f"Rejected: {exc}", file=sys.stderr)
        return 1
    print("Password updated and all existing sessions for that user were revoked.")
    return 0


def _cmd_reconcile_evidence(args: argparse.Namespace) -> int:
    from app.evidence.reconcile import reconcile
    from app.evidence.storage import EvidenceStorage

    settings = get_settings()
    factory = create_session_factory(create_db_engine(settings))
    report = reconcile(
        factory,
        EvidenceStorage(settings.evidence_storage_path),
        grace_seconds=settings.evidence_orphan_grace_seconds if not args.no_grace else 0,
        apply=args.apply,
    )
    mode = "applied" if args.apply else "dry run"
    print(f"Evidence reconciliation ({mode}):")
    print(f"  records checked:        {report.checked_records}")
    print(f"  staged files removed:   {report.staged_removed}")
    print(f"  orphans quarantined:    {len(report.orphans_quarantined)}")
    print(f"  missing files:          {len(report.missing_files)}")
    print(f"  hash/size mismatches:   {len(report.hash_mismatches)}")
    for evidence_id in report.missing_files + report.hash_mismatches:
        print(f"    integrity problem: evidence {evidence_id}")
    return 1 if report.missing_files or report.hash_mismatches else 0


def _cmd_pause_monitors(args: argparse.Namespace) -> int:
    from sqlalchemy import inspect

    from app.audit.service import service_actor
    from app.monitoring.service import pause_all_monitors

    engine = create_db_engine(get_settings())
    if not inspect(engine).has_table("monitors"):
        print("This database has no monitors (it predates monitoring); nothing to pause.")
        return 0
    factory = create_session_factory(engine)
    with session_scope(factory) as db:
        paused = pause_all_monitors(db, service_actor("operator-cli"), args.reason)
    print(f"Paused {paused} enabled monitor(s); analysts resume them explicitly.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("check-config", help="validate configuration").set_defaults(
        handler=_cmd_check_config
    )
    for name, handler, help_text in (
        ("create-admin", _cmd_create_admin, "create the first administrator"),
        ("reset-password", _cmd_reset_password, "set a new password and revoke sessions"),
    ):
        sub = commands.add_parser(name, help=help_text)
        sub.add_argument("--username", required=True)
        sub.add_argument(
            "--password-stdin", action="store_true", help="read the password from stdin"
        )
        sub.set_defaults(handler=handler)

    reconcile_parser = commands.add_parser(
        "reconcile-evidence", help="recover from interrupted evidence writes and verify hashes"
    )
    reconcile_parser.add_argument(
        "--apply", action="store_true", help="delete stale staged files and quarantine orphans"
    )
    reconcile_parser.add_argument(
        "--no-grace",
        action="store_true",
        help="ignore the grace period (only when writes are stopped)",
    )
    reconcile_parser.set_defaults(handler=_cmd_reconcile_evidence)

    pause_parser = commands.add_parser(
        "pause-monitors", help="pause every enabled monitor (for example after a restore)"
    )
    pause_parser.add_argument("--reason", choices=("operator", "restore"), default="operator")
    pause_parser.set_defaults(handler=_cmd_pause_monitors)

    args = parser.parse_args(argv)
    try:
        code: int = args.handler(args)
    except ConfigurationError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return code


if __name__ == "__main__":
    sys.exit(main())
