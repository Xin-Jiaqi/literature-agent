#!/usr/bin/env python3
"""Invite-only credentials and server-side sessions for the local pilot web app."""
from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timedelta, timezone

import bcrypt

from la_product_db import OwnershipError, ProductStore, ValidationError, _new_id, new_secret, now_iso


SESSION_HOURS = 24 * 7
MAX_FAILED_LOGINS = 10
LOGIN_WINDOW_MINUTES = 15
_DUMMY_PASSWORD_HASH = bcrypt.hashpw(b"dummy-password-for-timing-only", bcrypt.gensalt(rounds=12))


def _digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _parse_time(value):
    return datetime.fromisoformat(value)


class AuthService:
    def __init__(self, conn):
        self.conn = conn
        self.store = ProductStore(conn)

    def create_invite(self, email=None, expires_hours=168, max_uses=1):
        if email is not None:
            email = self._email(email)
        if not isinstance(expires_hours, int) or not 1 <= expires_hours <= 24 * 30:
            raise ValidationError("expires_hours must be 1-720")
        if not isinstance(max_uses, int) or not 1 <= max_uses <= 100:
            raise ValidationError("max_uses must be 1-100")
        code = new_secret(24)
        expires = datetime.now(timezone.utc) + timedelta(hours=expires_hours)
        self.conn.execute(
            "INSERT INTO product_invites "
            "(invite_id,email,code_hash,max_uses,used_count,expires_at,created_at) VALUES (?,?,?,?,?,?,?)",
            (_new_id("inv"), email, _digest(code), max_uses, 0,
             expires.replace(microsecond=0).isoformat(), now_iso()),
        )
        self.conn.commit()
        return code

    def register(self, invite_code, email, password, display_name="", remote_addr=""):
        email = self._email(email)
        password = self._password(password)
        if not isinstance(display_name, str) or len(display_name.strip()) > 100:
            raise ValidationError("invalid display_name")
        display_name = display_name.strip()
        code_hash = _digest(self._secret(invite_code, "invite_code"))
        now = datetime.now(timezone.utc)
        invite = self.conn.execute(
            "SELECT * FROM product_invites WHERE code_hash=? AND disabled_at IS NULL",
            (code_hash,),
        ).fetchone()
        if (not invite or invite["used_count"] >= invite["max_uses"]
                or _parse_time(invite["expires_at"]) <= now
                or (invite["email"] and invite["email"] != email)):
            self._record_attempt(email, remote_addr, False)
            raise ValidationError("invalid or expired invite")
        if self.conn.execute("SELECT 1 FROM product_users WHERE email=?", (email,)).fetchone():
            raise ValidationError("account already exists")
        password_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("ascii")
        stamp = now_iso()
        user_id = _new_id("usr")
        try:
            self.conn.execute(
                "INSERT INTO product_users (user_id,email,display_name,status,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?)",
                (user_id, email, display_name, "active", stamp, stamp),
            )
            self.conn.execute(
                "INSERT INTO product_credentials (owner_user_id,password_hash,created_at,updated_at) "
                "VALUES (?,?,?,?)", (user_id, password_hash, stamp, stamp)
            )
            self.conn.execute(
                "UPDATE product_invites SET used_count=used_count+1 WHERE invite_id=?",
                (invite["invite_id"],),
            )
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        self._record_attempt(email, remote_addr, True)
        return user_id, self.create_session(user_id)

    def login(self, email, password, remote_addr=""):
        email = self._email(email)
        password = self._password(password)
        if self._recent_failures(email, remote_addr) >= MAX_FAILED_LOGINS:
            raise ValidationError("too many login attempts; try later")
        row = self.conn.execute(
            "SELECT u.user_id,c.password_hash FROM product_users u "
            "JOIN product_credentials c ON c.owner_user_id=u.user_id "
            "WHERE u.email=? AND u.status='active'", (email,),
        ).fetchone()
        candidate_hash = row["password_hash"].encode("ascii") if row else _DUMMY_PASSWORD_HASH
        password_ok = bcrypt.checkpw(password.encode("utf-8"), candidate_hash)
        valid = bool(row) and password_ok
        self._record_attempt(email, remote_addr, valid)
        if not valid:
            raise OwnershipError("invalid credentials")
        return row["user_id"], self.create_session(row["user_id"])

    def create_session(self, owner_user_id):
        self.store._require_user(owner_user_id)
        token = new_secret(32)
        csrf = new_secret(24)
        now = datetime.now(timezone.utc)
        expires = now + timedelta(hours=SESSION_HOURS)
        self.conn.execute(
            "INSERT INTO product_sessions "
            "(session_id,owner_user_id,token_hash,csrf_hash,expires_at,created_at,last_seen_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (_new_id("ses"), owner_user_id, _digest(token), _digest(csrf),
             expires.replace(microsecond=0).isoformat(), now_iso(), now_iso()),
        )
        self.conn.commit()
        return {"token": token, "csrf": csrf, "expires_at": expires}

    def authenticate(self, token, touch=True):
        token = self._secret(token, "session")
        row = self.conn.execute(
            "SELECT s.*,u.email,u.display_name FROM product_sessions s "
            "JOIN product_users u ON u.user_id=s.owner_user_id "
            "WHERE s.token_hash=? AND s.revoked_at IS NULL AND u.status='active'",
            (_digest(token),),
        ).fetchone()
        if not row or _parse_time(row["expires_at"]) <= datetime.now(timezone.utc):
            raise OwnershipError("authentication required")
        if touch:
            self.conn.execute(
                "UPDATE product_sessions SET last_seen_at=? WHERE session_id=?",
                (now_iso(), row["session_id"]),
            )
            self.conn.commit()
        return row

    def verify_csrf(self, session_row, csrf_token):
        csrf_token = self._secret(csrf_token, "csrf")
        if not hmac.compare_digest(session_row["csrf_hash"], _digest(csrf_token)):
            raise OwnershipError("invalid csrf token")

    def logout(self, token):
        token = self._secret(token, "session")
        self.conn.execute(
            "UPDATE product_sessions SET revoked_at=? WHERE token_hash=? AND revoked_at IS NULL",
            (now_iso(), _digest(token)),
        )
        self.conn.commit()

    def _record_attempt(self, email, remote_addr, ok):
        self.conn.execute(
            "INSERT INTO auth_attempts (attempt_id,email,remote_addr,ok,attempted_at) VALUES (?,?,?,?,?)",
            (_new_id("att"), email, (remote_addr or "")[:128], int(bool(ok)), now_iso()),
        )
        self.conn.commit()

    def _recent_failures(self, email, remote_addr):
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=LOGIN_WINDOW_MINUTES)).isoformat()
        return self.conn.execute(
            "SELECT COUNT(*) FROM auth_attempts WHERE email=? AND remote_addr=? AND ok=0 AND attempted_at>=?",
            (email, (remote_addr or "")[:128], cutoff),
        ).fetchone()[0]

    def prune(self):
        """Remove expired sessions and old login-attempt telemetry."""
        now = datetime.now(timezone.utc)
        attempts_before = (now - timedelta(days=7)).isoformat()
        self.conn.execute("DELETE FROM product_sessions WHERE expires_at<?", (now.isoformat(),))
        self.conn.execute("DELETE FROM auth_attempts WHERE attempted_at<?", (attempts_before,))
        self.conn.commit()

    @staticmethod
    def _email(value):
        if not isinstance(value, str):
            raise ValidationError("invalid email")
        value = value.strip().lower()
        if len(value) > 254 or "@" not in value or any(ch.isspace() for ch in value):
            raise ValidationError("invalid email")
        return value

    @staticmethod
    def _password(value):
        if (not isinstance(value, str) or not 12 <= len(value) <= 128
                or len(value.encode("utf-8")) > 72):
            raise ValidationError("password must be 12-128 characters and at most 72 bytes")
        return value

    @staticmethod
    def _secret(value, field):
        if not isinstance(value, str) or not 16 <= len(value) <= 256:
            raise ValidationError(f"invalid {field}")
        return value
