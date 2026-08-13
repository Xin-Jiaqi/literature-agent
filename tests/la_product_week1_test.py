#!/usr/bin/env python3
"""Week-one campus pilot acceptance: explicit profiles + service boundary."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import literature_agent as la
from la_product_db import OwnershipError, ProductStore, ValidationError
from la_product_service import ProductService


def _expect(error_type, fn, message):
    try:
        fn()
    except error_type:
        return
    raise AssertionError(message)


def _insert_work(conn, work_id, title, abstract):
    conn.execute(
        "INSERT INTO works "
        "(work_id,title,normalized_title,authors,status,first_seen,latest_seen,metadata_json) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (work_id, title, title.lower().replace(" ", ""), "Test Author", "new",
         "2026-08-13", "2026-08-13", "{}"),
    )
    conn.execute(
        "INSERT INTO work_sources "
        "(work_id,source,version,title,url,abstract,abstract_truncated,date) VALUES (?,?,?,?,?,?,?,?)",
        (work_id, "test", "v1", title, "https://example.test/paper", abstract, 0, "2026-08-13"),
    )


def run_all():
    with tempfile.TemporaryDirectory(prefix="la_product_week1_") as tmp:
        conn = la.connect(os.path.join(tmp, "week1.db"))
        _insert_work(
            conn, "test:shared",
            "Unified symmetry framework for altermagnetic spin splitting",
            "We establish a generalized magnetic symmetry framework for spin splitting.",
        )
        _insert_work(
            conn, "test:bpve",
            "Shift current in sliding ferroelectric bilayers",
            "A novel first-principles mechanism connects sliding ferroelectricity and bulk photovoltaic response.",
        )
        _insert_work(
            conn, "test:battery",
            "Machine learning for sodium ion battery cathodes",
            "A systematic battery screening workflow for electrochemical storage.",
        )
        conn.commit()

        store = ProductStore(conn)
        service = ProductService(conn)
        alice = store.create_user("alice@example.edu", "Alice", "usr_alice")
        bob = store.create_user("bob@example.edu", "Bob", "usr_bob")
        alice_project = service.create_project(alice, {
            "title": "Altermagnet project",
            "description": "Magnetic symmetry and spin splitting",
            "daily_attention_limit": 1,
        })["project_id"]
        bob_project = service.create_project(bob, {
            "title": "BPVE project",
            "description": "Sliding ferroelectrics and photocurrent",
            "daily_attention_limit": 2,
        })["project_id"]

        service.confirm_profile(alice, alice_project, {
            "summary": "Track altermagnetic symmetry and spin splitting",
            "include_terms": ["altermagnetic", "spin splitting", "magnetic symmetry"],
            "exclude_terms": ["battery"],
        })
        service.confirm_profile(bob, bob_project, {
            "summary": "Track shift current in sliding ferroelectrics",
            "include_terms": ["shift current", "sliding ferroelectric", "bulk photovoltaic"],
            "exclude_terms": ["battery"],
        })
        work_ids = ["test:shared", "test:bpve", "test:battery"]
        alice_results = service.screen_project(alice, alice_project, work_ids, "2026-08-13")
        bob_results = service.screen_project(bob, bob_project, work_ids, "2026-08-13")
        alice_by_id = {item["work_id"]: item for item in alice_results}
        bob_by_id = {item["work_id"]: item for item in bob_results}

        assert alice_by_id["test:shared"]["priority"] == "MUST", alice_by_id
        assert alice_by_id["test:bpve"]["priority"] == "DROP", alice_by_id
        assert bob_by_id["test:bpve"]["priority"] == "MUST", bob_by_id
        assert bob_by_id["test:shared"]["priority"] == "DROP", bob_by_id
        assert alice_by_id["test:battery"]["priority"] == "DROP"
        assert bob_by_id["test:battery"]["priority"] == "DROP"
        stored_revision = conn.execute(
            "SELECT profile_revision,rank_score FROM user_recommendations WHERE recommendation_id=?",
            (alice_by_id["test:shared"]["recommendation_id"],),
        ).fetchone()
        assert stored_revision["profile_revision"] == 1 and stored_revision["rank_score"] == 12

        # Attention budget caps actionable recommendations and repeated runs are idempotent.
        assert sum(x["priority"] in ("MUST", "WEEKLY") for x in alice_results) <= 1
        before = conn.execute("SELECT COUNT(*) FROM user_recommendations").fetchone()[0]
        rerun = service.screen_project(alice, alice_project, work_ids, "2026-08-13")
        after = conn.execute("SELECT COUNT(*) FROM user_recommendations").fetchone()[0]
        assert before == after and len(rerun) == 3

        # Daily feed and decisions are authenticated by actor, not resource id alone.
        alice_feed = service.daily_feed(alice, "2026-08-13")
        bob_feed = service.daily_feed(bob, "2026-08-13")
        assert {x["project_id"] for x in alice_feed} == {alice_project}
        assert {x["project_id"] for x in bob_feed} == {bob_project}
        assert alice_feed[0]["priority"] == "MUST", "daily feed must put actionable papers first"
        alice_rec = alice_by_id["test:shared"]["recommendation_id"]
        service.decide(alice, alice_rec, {"disposition": "read", "note": "Read figures"})
        _expect(OwnershipError, lambda: service.decide(
            bob, alice_rec, {"disposition": "ignore"}
        ), "another actor must not decide the recommendation")

        # Boundary validation rejects mass assignment and unconfirmed profiles.
        _expect(ValidationError, lambda: service.create_project(alice, {
            "title": "Injected", "description": "Attempt", "owner_user_id": bob,
        }), "unknown owner field must be rejected")
        _expect(ValidationError, lambda: service.create_project(alice, {
            "title": 123, "description": "Wrong input type"
        }), "wrong input types must fail as validation errors")
        _expect(ValidationError, lambda: service.daily_feed(alice, "not-a-date"),
                "invalid feed dates must be rejected")
        _expect(ValidationError, lambda: service.confirm_profile(alice, alice_project, {
            "summary": "Empty", "include_terms": [], "exclude_terms": []
        }), "confirmed profiles require at least one include term")
        charlie = store.create_user("charlie@example.edu", "Charlie", "usr_charlie")
        charlie_project = service.create_project(charlie, {
            "title": "Draft", "description": "Profile is not confirmed"
        })["project_id"]
        store.set_profile(charlie, charlie_project, "Draft", ["draft"], [], confirmed=False)
        _expect(ValueError, lambda: service.screen_project(
            charlie, charlie_project, ["test:shared"], "2026-08-13"
        ), "unconfirmed profiles must not produce recommendations")

        # Cost summaries remain user-specific.
        store.record_usage(alice, "screen", 2000, alice_project, "test:shared")
        store.record_usage(bob, "screen", 500, bob_project, "test:bpve")
        assert service.usage_summary(alice)["estimated_cost_micros"] == 2000
        assert service.usage_summary(bob)["estimated_cost_micros"] == 500

        print("[PASS] explicit profiles produce different priorities for the same public works")
        print("[PASS] daily attention budgets cap actionable recommendations")
        print("[PASS] reruns are idempotent and feeds are actor-scoped")
        print("[PASS] service boundary rejects mass assignment and draft profiles")
        print("[PASS] usage summaries stay tenant-specific")
        print("product week-one test: ALL PASS")
    return True


if __name__ == "__main__":
    sys.exit(0 if run_all() else 1)
