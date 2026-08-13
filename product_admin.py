#!/usr/bin/env python3
"""Local-only administration for the invite pilot.

Invite secrets are printed once and never stored in plaintext.  All commands
operate on the configured SQLite database and are intentionally unavailable
from the HTTP application.
"""
import argparse
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import literature_agent as la
from la_product_auth import AuthService
from la_product_web import create_app


def _connect(db_path):
    return la.connect(db_path) if db_path else la.connect()


def _backup(db_path, output_dir, keep):
    conn = _connect(db_path)
    target_dir = Path(output_dir).expanduser().resolve()
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = target_dir / f"literature-pilot-{stamp}.db"
    destination = sqlite3.connect(target)
    try:
        conn.backup(destination)
        result = destination.execute("PRAGMA integrity_check").fetchone()[0]
        if result != "ok":
            raise RuntimeError(f"backup integrity check failed: {result}")
    finally:
        destination.close()
        conn.close()
    target.chmod(0o600)
    backups = sorted(target_dir.glob("literature-pilot-*.db"))
    for old in backups[:-keep]:
        old.unlink()
    return target


def main():
    parser = argparse.ArgumentParser(description="Literature Agent campus pilot admin")
    parser.add_argument("--db", help="override LITAGENT_DB for this command")
    sub = parser.add_subparsers(dest="command", required=True)
    invite = sub.add_parser("invite", help="create a time-limited invite")
    invite.add_argument("--email", help="optionally bind the invite to one email")
    invite.add_argument("--hours", type=int, default=168)
    invite.add_argument("--max-uses", type=int, default=1)
    sub.add_parser("invites", help="list invite metadata (never plaintext codes)")
    revoke = sub.add_parser("revoke-invite", help="disable one invite")
    revoke.add_argument("invite_id")
    backup = sub.add_parser("backup", help="create and verify a consistent SQLite backup")
    backup.add_argument("--output-dir", default="backups/pilot")
    backup.add_argument("--keep", type=int, default=14)
    sub.add_parser("status", help="show a privacy-safe service status summary")
    delete_user = sub.add_parser("delete-user", help="delete a user and all private product data")
    delete_user.add_argument("--email", required=True)
    serve = sub.add_parser("serve", help="run the pilot with a production WSGI server")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--dev-http", action="store_true",
                       help="allow non-secure cookies for local HTTP only")
    args = parser.parse_args()

    if args.command == "invite":
        conn = _connect(args.db)
        try:
            code = AuthService(conn).create_invite(args.email, args.hours, args.max_uses)
        finally:
            conn.close()
        print(json.dumps({"email": args.email.lower() if args.email else None,
                          "invite_code": code, "expires_hours": args.hours,
                          "max_uses": args.max_uses}, ensure_ascii=False, indent=2))
    elif args.command == "invites":
        conn = _connect(args.db)
        try:
            rows = conn.execute(
                "SELECT invite_id,email,max_uses,used_count,expires_at,created_at,disabled_at "
                "FROM product_invites ORDER BY created_at DESC"
            ).fetchall()
            print(json.dumps([dict(row) for row in rows], ensure_ascii=False, indent=2))
        finally:
            conn.close()
    elif args.command == "revoke-invite":
        conn = _connect(args.db)
        try:
            changed = conn.execute(
                "UPDATE product_invites SET disabled_at=? "
                "WHERE invite_id=? AND disabled_at IS NULL",
                (datetime.now(timezone.utc).replace(microsecond=0).isoformat(), args.invite_id),
            ).rowcount
            conn.commit()
        finally:
            conn.close()
        if changed != 1:
            raise SystemExit("active invite not found")
        print(json.dumps({"revoked": args.invite_id}, ensure_ascii=False))
    elif args.command == "backup":
        if not 1 <= args.keep <= 100:
            raise SystemExit("--keep must be 1-100")
        target = _backup(args.db, args.output_dir, args.keep)
        print(json.dumps({"backup": str(target), "verified": True}, ensure_ascii=False, indent=2))
    elif args.command == "status":
        conn = _connect(args.db)
        try:
            counts = {}
            for name in ("works", "product_users", "research_projects", "user_recommendations"):
                counts[name] = conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
            counts["active_sessions"] = conn.execute(
                "SELECT COUNT(*) FROM product_sessions WHERE revoked_at IS NULL AND expires_at>?",
                (datetime.now(timezone.utc).isoformat(),),
            ).fetchone()[0]
            counts["integrity"] = conn.execute("PRAGMA integrity_check").fetchone()[0]
            print(json.dumps(counts, ensure_ascii=False, indent=2))
        finally:
            conn.close()
    elif args.command == "delete-user":
        email = args.email.strip().lower()
        conn = _connect(args.db)
        try:
            row = conn.execute("SELECT user_id FROM product_users WHERE email=?", (email,)).fetchone()
            if not row:
                raise SystemExit("user not found")
            conn.execute("DELETE FROM product_users WHERE user_id=?", (row["user_id"],))
            conn.commit()
        finally:
            conn.close()
        print(json.dumps({"deleted_user": email}, ensure_ascii=False))
    elif args.command == "serve":
        if args.host not in ("127.0.0.1", "localhost") and args.dev_http:
            raise SystemExit("--dev-http may only bind to localhost")
        if args.db:
            os.environ["LITAGENT_DB"] = str(Path(args.db).expanduser().resolve())
        app = create_app(secure_cookies=not args.dev_http)
        if args.dev_http:
            app.run(host=args.host, port=args.port, debug=False)
        else:
            try:
                from waitress import serve
            except ImportError as exc:
                raise SystemExit("install requirements-web.txt before production serve") from exc
            serve(
                app,
                host=args.host,
                port=args.port,
                threads=4,
                ident="Literature-Agent",
                url_scheme="https",
                trusted_proxy="127.0.0.1" if args.host in ("127.0.0.1", "localhost") else None,
                trusted_proxy_count=1,
                trusted_proxy_headers={"x-forwarded-for", "x-forwarded-proto"},
                clear_untrusted_proxy_headers=True,
                expose_tracebacks=False,
                channel_timeout=60,
                max_request_body_size=1_000_000,
                max_request_header_size=32_768,
            )


if __name__ == "__main__":
    main()
