#!/usr/bin/env python3
"""Campus pilot tenant-isolation acceptance test (temporary DB only)."""
import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import literature_agent as la
from la_product_db import OwnershipError, ProductStore, ValidationError


def _expect(error_type, fn, message):
    try:
        fn()
    except error_type:
        return
    raise AssertionError(message)


def run_all():
    with tempfile.TemporaryDirectory(prefix="la_product_isolation_") as tmp:
        conn = la.connect(os.path.join(tmp, "pilot.db"))
        conn.execute(
            "INSERT INTO works "
            "(work_id,title,normalized_title,authors,doi,status,first_seen,latest_seen,metadata_json) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            ("doi:10.1000/shared", "Shared Paper", "sharedpaper", "A. Author",
             "10.1000/shared", "new", "2026-08-13", "2026-08-13", "{}"),
        )
        conn.commit()
        store = ProductStore(conn)

        alice = store.create_user("alice@example.edu", "Alice", "usr_alice")
        bob = store.create_user("bob@example.edu", "Bob", "usr_bob")
        alice_project = store.create_project(
            alice, "Altermagnetism", "Track symmetry-driven spin splitting.", 3, "prj_alice"
        )
        bob_project = store.create_project(
            bob, "Photocurrent", "Track nonlinear photocurrent mechanisms.", 5, "prj_bob"
        )

        # The same public work is reused, while the project seeds stay private.
        store.add_seed(alice, alice_project, "doi:10.1000/shared")
        store.add_seed(bob, bob_project, "doi:10.1000/shared")
        assert conn.execute("SELECT COUNT(*) FROM works").fetchone()[0] == 1
        assert [row["work_id"] for row in store.list_seeds(alice, alice_project)] == ["doi:10.1000/shared"]
        assert [row["work_id"] for row in store.list_seeds(bob, bob_project)] == ["doi:10.1000/shared"]
        _expect(OwnershipError, lambda: store.list_seeds(bob, alice_project),
                "another user must not read project seeds")

        store.set_profile(alice, alice_project, "Magnetic symmetry and altermagnets", ["spin splitting"], ["device only"])
        store.set_profile(bob, bob_project, "Bulk photovoltaic effect", ["shift current"], [])
        assert "altermagnets" in store.get_profile(alice, alice_project)["summary"]
        _expect(OwnershipError, lambda: store.get_profile(bob, alice_project),
                "another user must not read a research profile")

        alice_rec = store.add_recommendation(
            alice, alice_project, "doi:10.1000/shared", "2026-08-13", "MUST", "Matches symmetry project."
        )
        bob_rec = store.add_recommendation(
            bob, bob_project, "doi:10.1000/shared", "2026-08-13", "RADAR", "Peripheral mechanism."
        )
        store.decide(alice, alice_rec, "read", "Check Fig. 2")
        _expect(OwnershipError, lambda: store.decide(bob, alice_rec, "ignore"),
                "another user must not change a recommendation")
        assert store.list_recommendations(alice)[0]["priority"] == "MUST"
        assert store.list_recommendations(bob)[0]["recommendation_id"] == bob_rec

        alice_asset = store.add_private_asset(
            alice, alice_project, "pdf", f"users/{alice}/papers/shared.pdf",
            "doi:10.1000/shared", "a" * 64, 2048,
        )
        assert store.list_private_assets(alice, alice_project)[0]["asset_id"] == alice_asset
        _expect(OwnershipError, lambda: store.list_private_assets(bob, alice_project),
                "another user must not list private assets")
        _expect(ValidationError, lambda: store.add_private_asset(
            alice, alice_project, "pdf", f"users/{bob}/papers/stolen.pdf"
        ), "object keys must stay inside the owner's namespace")
        _expect(ValidationError, lambda: store.add_private_asset(
            alice, alice_project, "pdf", f"users/{alice}/../{bob}/stolen.pdf"
        ), "object keys must reject traversal segments")

        # The database also rejects mismatched owner/project pairs if a caller
        # bypasses the repository by mistake.
        try:
            conn.execute(
                "INSERT INTO project_seeds (project_id,owner_user_id,work_id,source,added_at) "
                "VALUES (?,?,?,?,?)",
                (alice_project, bob, "doi:10.1000/shared", "manual", "2026-08-13T00:00:00+00:00"),
            )
        except sqlite3.IntegrityError:
            conn.rollback()
        else:
            raise AssertionError("database must reject mismatched owner/project pairs")

        store.record_usage(alice, "screen", 1200, alice_project, "doi:10.1000/shared", "test", "small", 100, 20)
        store.record_usage(alice, "a_read", 8800, alice_project, "doi:10.1000/shared", "test", "large", 500, 100)
        store.record_usage(bob, "screen", 500, bob_project, "doi:10.1000/shared", "test", "small", 50, 5)
        assert store.usage_total_micros(alice) == 10000
        assert store.usage_total_micros(bob) == 500

        # SQLite foreign keys and owner-scoped repositories are both active.
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        print("[PASS] public work metadata is shared without duplication")
        print("[PASS] projects, profiles, seeds and recommendations are owner-scoped")
        print("[PASS] private asset keys cannot cross tenant namespaces")
        print("[PASS] model usage and cost are measured per user")
        print("product isolation test: ALL PASS")
    return True


if __name__ == "__main__":
    sys.exit(0 if run_all() else 1)
