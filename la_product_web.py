#!/usr/bin/env python3
"""Flask HTTP surface for the invite-only campus pilot."""
from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path

from flask import Flask, jsonify, make_response, request, send_from_directory
from pydantic import BaseModel, ConfigDict, Field, ValidationError as PydanticValidationError

from la_product_auth import AuthService
from la_product_db import OwnershipError, ValidationError
from la_product_service import ProductService


BASE = Path(__file__).resolve().parent
WEB_ROOT = BASE / "web"
SESSION_COOKIE = "la_session"
CSRF_COOKIE = "la_csrf"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class RegisterBody(StrictModel):
    invite_code: str = Field(min_length=16, max_length=256)
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=12, max_length=128)
    display_name: str = Field(default="", max_length=100)


class LoginBody(StrictModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=12, max_length=128)


class ProjectBody(StrictModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=8000)
    daily_attention_limit: int = Field(default=3, ge=1, le=20)


class ProfileBody(StrictModel):
    summary: str = Field(min_length=1, max_length=12000)
    include_terms: list[str] = Field(min_length=1, max_length=50)
    exclude_terms: list[str] = Field(default_factory=list, max_length=50)


class SeedBody(StrictModel):
    work_id: str = Field(min_length=1, max_length=512)
    source: str = Field(default="manual", pattern="^(manual|bibtex|ris|zotero)$")


class ScreenBody(StrictModel):
    work_ids: list[str] = Field(min_length=1, max_length=500)
    recommendation_date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")


class DecisionBody(StrictModel):
    disposition: str = Field(pattern="^(read|later|ignore)$")
    note: str = Field(default="", max_length=4000)


def _connect(db_path):
    import literature_agent as la
    return la.connect(db_path)


def create_app(db_path=None, secure_cookies=None):
    db_path = db_path or os.environ.get("LITAGENT_DB")
    if not db_path:
        from literature_config import DB_PATH
        db_path = DB_PATH
    secure_cookies = (os.environ.get("LITAGENT_WEB_SECURE_COOKIES", "1") != "0"
                      if secure_cookies is None else bool(secure_cookies))
    app = Flask(__name__, static_folder=str(WEB_ROOT / "static"), static_url_path="/static")
    app.config.update(DB_PATH=db_path, SECURE_COOKIES=secure_cookies, MAX_CONTENT_LENGTH=1_000_000)

    @app.after_request
    def security_headers(response):
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
            "connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Cache-Control"] = "no-store"
        if secure_cookies:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response

    @app.errorhandler(ValidationError)
    def domain_validation(exc):
        return _error(400, str(exc))

    @app.errorhandler(PydanticValidationError)
    def input_validation(_):
        return _error(422, "invalid request body")

    @app.errorhandler(OwnershipError)
    def ownership_error(exc):
        if str(exc) == "authentication required":
            return _error(401, "authentication required")
        return _error(403, "not authorized")

    @app.errorhandler(413)
    def too_large(_):
        return _error(413, "request too large")

    @contextmanager
    def services():
        conn = _connect(app.config["DB_PATH"])
        try:
            yield conn, AuthService(conn), ProductService(conn)
        finally:
            conn.close()

    def body(model):
        if not request.is_json:
            raise ValidationError("application/json required")
        return model.model_validate(request.get_json(silent=False))

    def actor():
        token = request.cookies.get(SESSION_COOKIE)
        if not token:
            raise OwnershipError("authentication required")
        with services() as (_, auth, __):
            try:
                return dict(auth.authenticate(token))
            except (OwnershipError, ValidationError):
                raise OwnershipError("authentication required") from None

    def require_write():
        session = actor()
        with services() as (_, auth, __):
            auth.verify_csrf(session, request.headers.get("X-CSRF-Token"))
        return session

    def set_auth_cookies(response, session):
        max_age = 7 * 24 * 60 * 60
        response.set_cookie(
            SESSION_COOKIE, session["token"], max_age=max_age, httponly=True,
            secure=secure_cookies, samesite="Strict", path="/",
        )
        response.set_cookie(
            CSRF_COOKIE, session["csrf"], max_age=max_age, httponly=False,
            secure=secure_cookies, samesite="Strict", path="/",
        )

    @app.get("/")
    def index():
        return send_from_directory(WEB_ROOT, "index.html")

    @app.get("/healthz")
    def health():
        with services() as (conn, _, __):
            conn.execute("SELECT 1").fetchone()
        return {"ok": True}

    @app.post("/v1/auth/register")
    def register():
        data = body(RegisterBody)
        with services() as (_, auth, __):
            try:
                user_id, session = auth.register(
                    data.invite_code, data.email, data.password, data.display_name, request.remote_addr or ""
                )
            except ValidationError:
                return _error(400, "registration unavailable")
        response = make_response(jsonify({"user_id": user_id}))
        set_auth_cookies(response, session)
        return response

    @app.post("/v1/auth/login")
    def login():
        data = body(LoginBody)
        with services() as (_, auth, __):
            try:
                user_id, session = auth.login(data.email, data.password, request.remote_addr or "")
            except OwnershipError:
                return _error(401, "invalid credentials")
        response = make_response(jsonify({"user_id": user_id}))
        set_auth_cookies(response, session)
        return response

    @app.post("/v1/auth/logout")
    def logout():
        require_write()
        token = request.cookies.get(SESSION_COOKIE)
        with services() as (_, auth, __):
            auth.logout(token)
        response = make_response(jsonify({"ok": True}))
        response.delete_cookie(SESSION_COOKIE, path="/")
        response.delete_cookie(CSRF_COOKIE, path="/")
        return response

    @app.get("/v1/me")
    def me():
        try:
            session = actor()
        except OwnershipError:
            return _error(401, "authentication required")
        return {"user_id": session["owner_user_id"], "email": session["email"],
                "display_name": session["display_name"]}

    @app.get("/v1/projects")
    def list_projects():
        session = actor()
        with services() as (conn, _, __):
            rows = conn.execute(
                "SELECT project_id,title,description,daily_attention_limit,status,created_at "
                "FROM research_projects WHERE owner_user_id=? ORDER BY created_at,project_id",
                (session["owner_user_id"],),
            ).fetchall()
        return [{key: row[key] for key in row.keys()} for row in rows]

    @app.post("/v1/projects")
    def create_project():
        session = require_write()
        data = body(ProjectBody)
        with services() as (_, __, service):
            result = service.create_project(session["owner_user_id"], data.model_dump())
        return jsonify(result), 201

    @app.put("/v1/projects/<project_id>/profile")
    def confirm_profile(project_id):
        session = require_write()
        data = body(ProfileBody)
        with services() as (_, __, service):
            return service.confirm_profile(session["owner_user_id"], project_id, data.model_dump())

    @app.post("/v1/projects/<project_id>/seeds")
    def add_seed(project_id):
        session = require_write()
        data = body(SeedBody)
        with services() as (_, __, service):
            return service.add_seed(session["owner_user_id"], project_id, data.model_dump())

    @app.post("/v1/projects/<project_id>/screen")
    def screen(project_id):
        session = require_write()
        data = body(ScreenBody)
        with services() as (_, __, service):
            return service.screen_project(
                session["owner_user_id"], project_id, data.work_ids, data.recommendation_date
            )

    @app.get("/v1/papers/recent")
    def recent_papers():
        actor()
        try:
            limit = max(1, min(100, int(request.args.get("limit", 50))))
        except ValueError:
            raise ValidationError("invalid limit") from None
        with services() as (conn, _, __):
            rows = conn.execute(
                "SELECT w.work_id,w.title,w.authors,w.doi,w.arxiv_id,w.journal,w.first_seen,"
                "COALESCE((SELECT ws.abstract FROM work_sources ws WHERE ws.work_id=w.work_id "
                "ORDER BY ws.abstract_truncated ASC,length(ws.abstract) DESC LIMIT 1),'') AS abstract "
                "FROM works w WHERE w.work_id NOT LIKE 'test:%' AND w.work_id NOT LIKE 'sim-%' "
                "ORDER BY w.first_seen DESC,w.work_id LIMIT ?", (limit,),
            ).fetchall()
        return [{key: row[key] for key in row.keys()} for row in rows]

    @app.get("/v1/recommendations/daily")
    def daily():
        session = actor()
        with services() as (_, __, service):
            return service.daily_feed(session["owner_user_id"], request.args.get("recommendation_date"))

    @app.post("/v1/recommendations/<recommendation_id>/decision")
    def decide(recommendation_id):
        session = require_write()
        data = body(DecisionBody)
        with services() as (_, __, service):
            return service.decide(session["owner_user_id"], recommendation_id, data.model_dump())

    @app.get("/v1/usage/summary")
    def usage():
        session = actor()
        with services() as (_, __, service):
            return service.usage_summary(session["owner_user_id"])

    return app


def _error(status, message):
    return jsonify({"error": {"message": message}}), status
