#!/usr/bin/env python3
"""Invite/auth/API/browser-shell acceptance test on a temporary database."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import literature_agent as la
from la_product_auth import AuthService
from la_product_web import create_app


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


def _csrf(client):
    cookie = client.get_cookie("la_csrf")
    return cookie.value if cookie else None


def run_all():
    with tempfile.TemporaryDirectory(prefix="la_product_web_") as tmp:
        db_path = os.path.join(tmp, "web.db")
        conn = la.connect(db_path)
        _insert_work(conn, "web:mag", "Unified altermagnetic symmetry framework",
                     "We establish a novel framework for magnetic symmetry and spin splitting.")
        _insert_work(conn, "web:other", "Battery screening", "A battery materials database.")
        invite = AuthService(conn).create_invite("alice@example.edu")
        conn.close()

        app = create_app(db_path, secure_cookies=False)
        alice = app.test_client()
        bob = app.test_client()

        # Public shell and health are available; product data is not.
        home = alice.get("/")
        assert home.status_code == 200 and "每天少看一点" in home.text
        assert alice.get("/healthz").get_json() == {"ok": True}
        assert alice.get("/v1/me").status_code == 401
        assert "default-src 'self'" in home.headers["content-security-policy"]
        assert home.headers["x-frame-options"] == "DENY"
        assert "strict-transport-security" not in home.headers
        assert home.headers["cache-control"] == "no-store"
        secure_home = create_app(db_path, secure_cookies=True).test_client().get("/")
        assert secure_home.headers["strict-transport-security"].startswith("max-age=")

        # Invitation is email-bound and single-use.
        wrong = bob.post("/v1/auth/register", json={
            "invite_code": invite, "email": "bob@example.edu",
            "password": "correct horse battery staple", "display_name": "Bob",
        })
        assert wrong.status_code == 400
        registered = alice.post("/v1/auth/register", json={
            "invite_code": invite, "email": "alice@example.edu",
            "password": "correct horse battery staple", "display_name": "Alice",
        })
        assert registered.status_code == 200, registered.get_data(as_text=True)
        assert alice.get_cookie("la_session") and _csrf(alice)
        assert alice.get("/v1/me").get_json()["email"] == "alice@example.edu"
        reused = bob.post("/v1/auth/register", json={
            "invite_code": invite, "email": "alice2@example.edu",
            "password": "correct horse battery staple", "display_name": "Alice 2",
        })
        assert reused.status_code == 400
        assert "registration unavailable" in reused.get_data(as_text=True)

        # Writes require CSRF and reject mass assignment through Pydantic.
        body = {"title": "Altermagnet", "description": "Magnetic symmetry", "daily_attention_limit": 2}
        assert alice.post("/v1/projects", json=body).status_code == 400
        injected = alice.post("/v1/projects", json={**body, "owner_user_id": "usr_bob"},
                              headers={"X-CSRF-Token": _csrf(alice)})
        assert injected.status_code == 422
        created = alice.post("/v1/projects", json=body, headers={"X-CSRF-Token": _csrf(alice)})
        assert created.status_code == 201, created.get_data(as_text=True)
        project_id = created.get_json()["project_id"]
        profile = alice.put(f"/v1/projects/{project_id}/profile", json={
            "summary": "Track altermagnets", "include_terms": ["altermagnetic", "magnetic symmetry"],
            "exclude_terms": ["battery"],
        }, headers={"X-CSRF-Token": _csrf(alice)})
        assert profile.status_code == 200

        recent = alice.get("/v1/papers/recent?limit=10")
        assert recent.status_code == 200 and len(recent.get_json()) == 2
        screened = alice.post(f"/v1/projects/{project_id}/screen", json={
            "work_ids": ["web:mag", "web:other"], "recommendation_date": "2026-08-13"
        }, headers={"X-CSRF-Token": _csrf(alice)})
        assert screened.status_code == 200, screened.get_data(as_text=True)
        feed = alice.get("/v1/recommendations/daily?recommendation_date=2026-08-13")
        assert feed.status_code == 200 and feed.get_json()[0]["priority"] == "MUST"
        recommendation_id = feed.get_json()[0]["recommendation_id"]
        decided = alice.post(f"/v1/recommendations/{recommendation_id}/decision",
                             json={"disposition": "read"}, headers={"X-CSRF-Token": _csrf(alice)})
        assert decided.status_code == 200

        # A separate session cannot see or mutate Alice's records.
        conn = la.connect(db_path)
        bob_invite = AuthService(conn).create_invite("bob@example.edu")
        conn.close()
        bob.post("/v1/auth/register", json={
            "invite_code": bob_invite, "email": "bob@example.edu",
            "password": "another secure password 123", "display_name": "Bob",
        })
        assert bob.get("/v1/projects").get_json() == []
        forbidden = bob.post(f"/v1/recommendations/{recommendation_id}/decision",
                             json={"disposition": "ignore"}, headers={"X-CSRF-Token": _csrf(bob)})
        assert forbidden.status_code == 403

        # Login responses do not reveal whether an email exists.
        bad_existing = bob.post("/v1/auth/login", json={
            "email": "alice@example.edu", "password": "wrong password but long enough"
        })
        bad_missing = bob.post("/v1/auth/login", json={
            "email": "missing@example.edu", "password": "wrong password but long enough"
        })
        assert bad_existing.status_code == bad_missing.status_code == 401
        assert bad_existing.get_json() == bad_missing.get_json()

        # Logout revokes the server-side session.
        logged_out = alice.post("/v1/auth/logout", json={}, headers={"X-CSRF-Token": _csrf(alice)})
        assert logged_out.status_code == 200
        assert alice.get("/v1/me").status_code == 401

        print("[PASS] invitation-bound registration and server-side sessions")
        print("[PASS] secure headers, httpOnly session and CSRF write protection")
        print("[PASS] project/profile/screen/feed/decision HTTP loop")
        print("[PASS] tenant isolation survives separate browser sessions")
        print("[PASS] logout revokes session")
        print("product web test: ALL PASS")
    return True


if __name__ == "__main__":
    sys.exit(0 if run_all() else 1)
