"""
Агрегатор сырых данных от коллекторов в аналитические таблицы.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List, Optional

import pandas as pd


def _canonical_name(
    name: str,
    email: str,
    team_members: List[str],
) -> str:
    """
    Возвращает каноническое имя участника.
    Если список участников задан — сопоставляет по подстроке.
    Иначе возвращает отображаемое имя или email.
    """
    if not team_members:
        return name or email or "Unknown"

    name_lower = name.lower()
    email_lower = email.lower()
    for member in team_members:
        m = member.lower()
        if m in name_lower or m in email_lower or name_lower in m or email_lower == m:
            return member  # возвращаем оригинальное написание из конфига

    return name or email or "Unknown"


class DataAggregator:
    """Агрегирует данные коллекторов в сводные таблицы."""

    def __init__(
        self,
        commits: List[Dict[str, Any]],
        prs: List[Dict[str, Any]],
        reviews: List[Dict[str, Any]],
        work_items: List[Dict[str, Any]],
        team_members: Optional[List[str]] = None,
    ) -> None:
        self.commits = commits
        self.prs = prs
        self.reviews = reviews
        self.work_items = work_items
        self.team_members: List[str] = team_members or []

    # ------------------------------------------------------------------
    # Публичные методы
    # ------------------------------------------------------------------

    def get_summary(self) -> pd.DataFrame:
        """Одна строка на участника со всеми метриками за период."""
        persons: Dict[str, Dict[str, Any]] = defaultdict(lambda: {
            "person": "",
            "commits": 0,
            "lines_added": 0,
            "lines_deleted": 0,
            "prs_created": 0,
            "prs_merged": 0,
            "prs_abandoned": 0,
            "reviews_given": 0,
            "reviews_approved": 0,
            "reviews_suggestions": 0,
            "reviews_rejected": 0,
            "pr_comments": 0,
            "wi_created": 0,
            "wi_closed": 0,
            "wi_assigned": 0,
        })

        # Коммиты
        for c in self.commits:
            key = self._person_key(c.get("author_name", ""), c.get("author_email", ""))
            d = persons[key]
            d["person"] = key
            d["commits"] += 1
            d["lines_added"] += c.get("additions", 0) or 0
            d["lines_deleted"] += c.get("deletions", 0) or 0

        # Pull Requests
        for pr in self.prs:
            key = self._person_key(pr.get("author_name", ""), pr.get("author_email", ""))
            d = persons[key]
            d["person"] = key
            d["prs_created"] += 1
            status = (pr.get("status") or "").lower()
            if pr.get("is_merged"):
                d["prs_merged"] += 1
            elif status == "abandoned":
                d["prs_abandoned"] += 1

        # Ревью и комментарии
        for r in self.reviews:
            name = r.get("reviewer_name", "")
            email = r.get("reviewer_email", "")
            key = self._person_key(name, email)
            d = persons[key]
            d["person"] = key

            if r.get("record_type") == "review":
                d["reviews_given"] += 1
                vote = r.get("vote", 0) or 0
                if vote == 10:
                    d["reviews_approved"] += 1
                elif vote == 5:
                    d["reviews_suggestions"] += 1
                elif vote == -10:
                    d["reviews_rejected"] += 1
            elif r.get("record_type") == "comment":
                d["pr_comments"] += r.get("comment_count", 1) or 1

        # Work Items
        for wi in self.work_items:
            name = wi.get("person_name", "")
            email = wi.get("person_email", "")
            key = self._person_key(name, email)
            d = persons[key]
            d["person"] = key
            rt = wi.get("record_type", "")
            if rt == "created":
                d["wi_created"] += 1
            elif rt == "closed":
                d["wi_closed"] += 1
            elif rt == "assigned":
                d["wi_assigned"] += 1

        if not persons:
            return pd.DataFrame(columns=[
                "person", "commits", "lines_added", "lines_deleted",
                "prs_created", "prs_merged", "prs_abandoned",
                "reviews_given", "reviews_approved", "reviews_suggestions", "reviews_rejected",
                "pr_comments", "wi_created", "wi_closed", "wi_assigned",
            ])

        df = pd.DataFrame(list(persons.values()))
        # Убираем "Unknown" если есть реальные данные
        df = df[df["person"] != ""]
        df = df.sort_values("commits", ascending=False).reset_index(drop=True)
        return df

    def get_timeline(self) -> pd.DataFrame:
        """Одна строка на (участник, неделя)."""
        rows: Dict[tuple, Dict[str, Any]] = defaultdict(lambda: {
            "person": "",
            "week": "",
            "commits": 0,
            "prs_created": 0,
            "prs_merged": 0,
            "reviews_given": 0,
            "pr_comments": 0,
            "wi_created": 0,
            "wi_closed": 0,
        })

        for c in self.commits:
            key = (
                self._person_key(c.get("author_name", ""), c.get("author_email", "")),
                c.get("week", ""),
            )
            rows[key]["person"] = key[0]
            rows[key]["week"] = key[1]
            rows[key]["commits"] += 1

        for pr in self.prs:
            key = (
                self._person_key(pr.get("author_name", ""), pr.get("author_email", "")),
                pr.get("week", ""),
            )
            rows[key]["person"] = key[0]
            rows[key]["week"] = key[1]
            rows[key]["prs_created"] += 1
            if pr.get("is_merged"):
                rows[key]["prs_merged"] += 1

        for r in self.reviews:
            key = (
                self._person_key(r.get("reviewer_name", ""), r.get("reviewer_email", "")),
                r.get("week", ""),
            )
            rows[key]["person"] = key[0]
            rows[key]["week"] = key[1]
            if r.get("record_type") == "review":
                rows[key]["reviews_given"] += 1
            elif r.get("record_type") == "comment":
                rows[key]["pr_comments"] += r.get("comment_count", 1) or 1

        for wi in self.work_items:
            key = (
                self._person_key(wi.get("person_name", ""), wi.get("person_email", "")),
                wi.get("week", ""),
            )
            rows[key]["person"] = key[0]
            rows[key]["week"] = key[1]
            rt = wi.get("record_type", "")
            if rt == "created":
                rows[key]["wi_created"] += 1
            elif rt == "closed":
                rows[key]["wi_closed"] += 1

        if not rows:
            return pd.DataFrame(columns=[
                "person", "week", "commits", "prs_created", "prs_merged",
                "reviews_given", "pr_comments", "wi_created", "wi_closed",
            ])

        df = pd.DataFrame(list(rows.values()))
        df = df[df["person"] != ""]
        df = df.sort_values(["week", "person"]).reset_index(drop=True)
        return df

    def get_weekly_totals(self) -> pd.DataFrame:
        """Суммарные метрики по неделям (все участники вместе)."""
        timeline = self.get_timeline()
        if timeline.empty:
            return timeline

        numeric_cols = [c for c in timeline.columns if c not in ("person", "week")]
        totals = timeline.groupby("week")[numeric_cols].sum().reset_index()
        totals = totals.sort_values("week").reset_index(drop=True)
        return totals

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _person_key(self, name: str, email: str) -> str:
        return _canonical_name(name, email, self.team_members)
