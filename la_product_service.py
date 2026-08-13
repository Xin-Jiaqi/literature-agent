#!/usr/bin/env python3
"""Framework-neutral application service for the campus pilot.

Future HTTP routes should authenticate a user, pass that user id as ``actor``,
and delegate here. Keeping authorization and validation below the route layer
prevents a web framework migration from weakening tenant isolation.
"""
from __future__ import annotations

import json
from datetime import date

from la_product_db import ProductStore, ValidationError
from la_project_screen import classify_for_profile, profile_from_row, sort_key


class ProductService:
    def __init__(self, conn):
        self.conn = conn
        self.store = ProductStore(conn)

    def create_project(self, actor, payload):
        payload = self._mapping(payload)
        allowed = {"title", "description", "daily_attention_limit"}
        self._reject_unknown(payload, allowed)
        project_id = self.store.create_project(
            actor,
            payload.get("title"),
            payload.get("description"),
            payload.get("daily_attention_limit", 3),
        )
        return self._row(self.store.get_project(actor, project_id))

    def confirm_profile(self, actor, project_id, payload):
        payload = self._mapping(payload)
        allowed = {"summary", "include_terms", "exclude_terms"}
        self._reject_unknown(payload, allowed)
        include_terms = payload.get("include_terms", [])
        if not isinstance(include_terms, list) or not include_terms:
            raise ValidationError("confirmed profile requires include_terms")
        revision = self.store.set_profile(
            actor,
            project_id,
            payload.get("summary"),
            include_terms,
            payload.get("exclude_terms", []),
            confirmed=True,
        )
        profile = self._row(self.store.get_profile(actor, project_id))
        profile["include_terms"] = json.loads(profile.pop("include_terms_json"))
        profile["exclude_terms"] = json.loads(profile.pop("exclude_terms_json"))
        assert profile["revision"] == revision
        return profile

    def add_seed(self, actor, project_id, payload):
        payload = self._mapping(payload)
        self._reject_unknown(payload, {"work_id", "source"})
        self.store.add_seed(actor, project_id, payload.get("work_id"), payload.get("source", "manual"))
        return {"ok": True, "project_id": project_id, "work_id": payload.get("work_id")}

    def screen_project(self, actor, project_id, work_ids, recommendation_date=None):
        project = self.store.get_project(actor, project_id)
        if project is None:
            raise ValidationError("project not found")
        profile = profile_from_row(self.store.get_profile(actor, project_id))
        recommendation_date = recommendation_date or date.today().isoformat()
        if not isinstance(work_ids, list) or not 1 <= len(work_ids) <= 500:
            raise ValidationError("work_ids must contain 1-500 items")

        seen = set()
        candidates = []
        for work_id in work_ids:
            if not isinstance(work_id, str) or not work_id.strip():
                raise ValidationError("each work_id must be a non-empty string")
            if work_id in seen:
                continue
            seen.add(work_id)
            row = self.conn.execute(
                "SELECT w.work_id,w.title,w.authors,w.doi,w.arxiv_id,"
                "COALESCE((SELECT ws.abstract FROM work_sources ws WHERE ws.work_id=w.work_id "
                "ORDER BY ws.abstract_truncated ASC, length(ws.abstract) DESC LIMIT 1), '') AS abstract,"
                "COALESCE((SELECT ws.abstract_truncated FROM work_sources ws WHERE ws.work_id=w.work_id "
                "ORDER BY ws.abstract_truncated ASC, length(ws.abstract) DESC LIMIT 1), 1) AS abstract_truncated "
                "FROM works w WHERE w.work_id=?",
                (work_id,),
            ).fetchone()
            if row is None:
                raise ValidationError(f"unknown work_id: {work_id}")
            result = classify_for_profile(
                row["title"], row["abstract"], profile, bool(row["abstract_truncated"])
            )
            candidates.append((row, result))

        candidates.sort(key=lambda item: sort_key(item[1]), reverse=True)
        attention_limit = int(project["daily_attention_limit"])
        actionable_used = 0
        output = []
        for work, result in candidates:
            priority = result["priority"]
            if priority in ("MUST", "WEEKLY"):
                if actionable_used >= attention_limit:
                    priority = "RADAR"
                    result = dict(result)
                    result["priority"] = priority
                    result["reason"] += "；受每日注意力预算限制，降为 RADAR"
                else:
                    actionable_used += 1
            reason_payload = json.dumps(result, ensure_ascii=False, sort_keys=True)
            recommendation_id = self._upsert_recommendation(
                actor, project_id, work["work_id"], recommendation_date, priority, reason_payload,
                result["profile_revision"], result["score"],
            )
            output.append({
                "recommendation_id": recommendation_id,
                "work_id": work["work_id"],
                "title": work["title"],
                **result,
            })
        return output

    def daily_feed(self, actor, recommendation_date=None):
        rows = self.store.list_recommendations(actor, recommendation_date or date.today().isoformat())
        return [self._public_recommendation(row) for row in rows]

    def decide(self, actor, recommendation_id, payload):
        payload = self._mapping(payload)
        self._reject_unknown(payload, {"disposition", "note"})
        self.store.decide(actor, recommendation_id, payload.get("disposition"), payload.get("note", ""))
        return {"ok": True, "recommendation_id": recommendation_id}

    def usage_summary(self, actor):
        return {"owner_user_id": actor, "estimated_cost_micros": self.store.usage_total_micros(actor)}

    def _upsert_recommendation(self, actor, project_id, work_id, day, priority, reason,
                               profile_revision, rank_score):
        existing = self.conn.execute(
            "SELECT recommendation_id FROM user_recommendations WHERE owner_user_id=? AND project_id=? "
            "AND work_id=? AND recommendation_date=?",
            (actor, project_id, work_id, day),
        ).fetchone()
        if existing:
            self.conn.execute(
                "UPDATE user_recommendations SET priority=?, profile_revision=?, rank_score=?, reason=? "
                "WHERE recommendation_id=? AND owner_user_id=?",
                (priority, profile_revision, rank_score, reason, existing["recommendation_id"], actor),
            )
            self.conn.commit()
            return existing["recommendation_id"]
        return self.store.add_recommendation(
            actor, project_id, work_id, day, priority, reason, profile_revision, rank_score
        )

    @staticmethod
    def _mapping(payload):
        if not isinstance(payload, dict):
            raise ValidationError("payload must be an object")
        return payload

    @staticmethod
    def _reject_unknown(payload, allowed):
        unknown = set(payload) - allowed
        if unknown:
            raise ValidationError("unknown fields: " + ", ".join(sorted(unknown)))

    @staticmethod
    def _row(row):
        if row is None:
            return None
        return {key: row[key] for key in row.keys()}

    @staticmethod
    def _public_recommendation(row):
        data = {key: row[key] for key in row.keys()}
        try:
            data["explanation"] = json.loads(data.pop("reason"))
        except (TypeError, ValueError, json.JSONDecodeError):
            data["explanation"] = {"reason": data.pop("reason", "")}
        return data
