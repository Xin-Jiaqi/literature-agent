#!/usr/bin/env python3
"""Local pilot administration and online-backup acceptance test."""
import os
import sqlite3
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import literature_agent as la
from la_product_auth import AuthService
from product_admin import _backup


def run_all():
    with tempfile.TemporaryDirectory(prefix="la_product_admin_") as tmp:
        db_path = os.path.join(tmp, "pilot.db")
        backup_dir = os.path.join(tmp, "backups")
        conn = la.connect(db_path)
        invite = AuthService(conn).create_invite(None, expires_hours=24, max_uses=3)
        assert len(invite) >= 16
        conn.close()

        target = _backup(db_path, backup_dir, keep=2)
        assert target.exists()
        restored = sqlite3.connect(target)
        try:
            assert restored.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            row = restored.execute(
                "SELECT email,max_uses,used_count FROM product_invites"
            ).fetchone()
            assert row == (None, 3, 0)
        finally:
            restored.close()

        # Rotation keeps only the requested number of verified snapshots.
        _backup(db_path, backup_dir, keep=1)
        assert len(os.listdir(backup_dir)) == 1

        conn = la.connect(db_path)
        auth = AuthService(conn)
        code = auth.create_invite("cleanup@example.edu")
        auth.register(code, "cleanup@example.edu", "a sufficiently long password")
        conn.close()
        subprocess.run(
            [sys.executable, os.path.join(os.path.dirname(__file__), "..", "product_admin.py"),
             "--db", db_path, "delete-user", "--email", "cleanup@example.edu"],
            check=True, capture_output=True, text=True,
        )
        conn = la.connect(db_path)
        try:
            assert conn.execute("SELECT COUNT(*) FROM product_users").fetchone()[0] == 0
            assert conn.execute("SELECT COUNT(*) FROM product_sessions").fetchone()[0] == 0
        finally:
            conn.close()
        print("[PASS] generic and email-bound invitation administration")
        print("[PASS] SQLite online backup, integrity verification and rotation")
        print("[PASS] user deletion cascades through private product data")
        print("product admin test: ALL PASS")
    return True


if __name__ == "__main__":
    sys.exit(0 if run_all() else 1)
