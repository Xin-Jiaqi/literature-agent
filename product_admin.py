#!/usr/bin/env python3
"""Local-only administration for the invite pilot."""
import argparse
import json

import literature_agent as la
from la_product_auth import AuthService
from la_product_web import create_app


def main():
    parser = argparse.ArgumentParser(description="Literature Agent campus pilot admin")
    sub = parser.add_subparsers(dest="command", required=True)
    invite = sub.add_parser("invite", help="create a time-limited invite")
    invite.add_argument("--email", required=True)
    invite.add_argument("--hours", type=int, default=168)
    serve = sub.add_parser("serve", help="run the local pilot web app")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--dev-http", action="store_true",
                       help="allow non-secure cookies for local HTTP only")
    args = parser.parse_args()

    if args.command == "invite":
        conn = la.connect()
        try:
            code = AuthService(conn).create_invite(args.email, args.hours)
        finally:
            conn.close()
        print(json.dumps({"email": args.email.lower(), "invite_code": code,
                          "expires_hours": args.hours}, ensure_ascii=False, indent=2))
    elif args.command == "serve":
        if args.host not in ("127.0.0.1", "localhost") and args.dev_http:
            raise SystemExit("--dev-http may only bind to localhost")
        app = create_app(secure_cookies=not args.dev_http)
        app.run(host=args.host, port=args.port, debug=False)


if __name__ == "__main__":
    main()
