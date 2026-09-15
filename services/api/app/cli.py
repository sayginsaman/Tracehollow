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

    args = parser.parse_args(argv)
    try:
        code: int = args.handler(args)
    except ConfigurationError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return code


if __name__ == "__main__":
    sys.exit(main())
