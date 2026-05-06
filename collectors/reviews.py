"""
Сборщик данных о ревью (голоса рецензентов и комментарии) из Pull Request'ов.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from azure.devops.v7_1.git.models import GitPullRequestSearchCriteria

from collectors.base import BaseCollector


def _week_label(dt: datetime) -> str:
    return f"{dt.isocalendar()[0]}-W{dt.isocalendar()[1]:02d}"


def _safe_dt(value) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except Exception:
        return None


_VOTE_LABELS: Dict[int, str] = {
    10: "approved",
    5: "approved_with_suggestions",
    0: "no_vote",
    -5: "waiting_for_author",
    -10: "rejected",
}


class ReviewsCollector(BaseCollector):
    """Собирает ревью (голоса и комментарии) из PR всех репозиториев."""

    def collect(self) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []

        search_criteria = GitPullRequestSearchCriteria(status="all")

        for project in self.config.projects:
            try:
                repos = self.git_client.get_repositories(project=project)
            except Exception as exc:
                print(f"  [WARN] Не удалось получить репозитории проекта '{project}': {exc}")
                continue

            for repo in repos:
                prs = self._get_all_prs(repo.id, project, search_criteria)
                for pr in prs:
                    pr_date = _safe_dt(pr.closed_date) or _safe_dt(pr.creation_date)
                    pr_date = pr_date or self.config.date_from
                    pr_id = pr.pull_request_id

                    # --- Голоса рецензентов ---
                    try:
                        reviewers = self.git_client.get_pull_request_reviewers(
                            repository_id=repo.id,
                            pull_request_id=pr_id,
                            project=project,
                        )
                    except Exception as exc:
                        print(f"  [WARN] Не удалось получить рецензентов PR#{pr_id}: {exc}")
                        reviewers = []

                    for reviewer in reviewers:
                        reviewer_name: str = reviewer.display_name or ""
                        reviewer_email: str = (reviewer.unique_name or "").lower()
                        vote: int = reviewer.vote or 0

                        if self.config.team_members and not self._matches_member(
                            reviewer_name, reviewer_email
                        ):
                            continue

                        results.append(
                            {
                                "record_type": "review",
                                "reviewer_name": reviewer_name,
                                "reviewer_email": reviewer_email,
                                "date": pr_date,
                                "week": _week_label(pr_date),
                                "project": project,
                                "repo": repo.name,
                                "pr_id": pr_id,
                                "vote": vote,
                                "vote_label": _VOTE_LABELS.get(vote, "unknown"),
                            }
                        )

                    # --- Комментарии в тредах ---
                    try:
                        threads = self.git_client.get_threads(
                            repository_id=repo.id,
                            pull_request_id=pr_id,
                            project=project,
                        )
                    except Exception as exc:
                        print(f"  [WARN] Не удалось получить треды PR#{pr_id}: {exc}")
                        threads = []

                    for thread in threads:
                        comments = thread.comments or []
                        # Группируем по автору внутри треда
                        authors_in_thread: Dict[str, int] = {}
                        for comment in comments:
                            if (comment.comment_type or "").lower() == "system":
                                continue
                            if comment.author is None:
                                continue
                            author_name: str = comment.author.display_name or ""
                            author_email: str = (comment.author.unique_name or "").lower()
                            key = author_email or author_name.lower()
                            authors_in_thread[key] = authors_in_thread.get(key, 0) + 1
                            # Сохраним имя для восстановления
                            if not hasattr(thread, "_author_map"):
                                thread._author_map = {}
                            thread._author_map[key] = (author_name, author_email)

                        for key, count in authors_in_thread.items():
                            author_map = getattr(thread, "_author_map", {})
                            a_name, a_email = author_map.get(key, (key, key))

                            if self.config.team_members and not self._matches_member(
                                a_name, a_email
                            ):
                                continue

                            comment_date = _safe_dt(
                                getattr(thread, "published_date", None)
                            ) or pr_date

                            results.append(
                                {
                                    "record_type": "comment",
                                    "reviewer_name": a_name,
                                    "reviewer_email": a_email,
                                    "date": comment_date,
                                    "week": _week_label(comment_date),
                                    "project": project,
                                    "repo": repo.name,
                                    "pr_id": pr_id,
                                    "thread_id": thread.id,
                                    "comment_count": count,
                                    "vote": 0,
                                    "vote_label": "comment",
                                }
                            )

        return results

    # ------------------------------------------------------------------

    def _get_all_prs(self, repo_id: str, project: str, criteria) -> list:
        results = []
        skip = 0
        page_size = 100
        while True:
            try:
                prs = self.git_client.get_pull_requests(
                    repository_id=repo_id,
                    search_criteria=criteria,
                    project=project,
                    top=page_size,
                    skip=skip,
                )
            except Exception as exc:
                print(f"  [WARN] Ошибка пагинации PR: {exc}")
                break

            if not prs:
                break

            for pr in prs:
                creation_date = _safe_dt(pr.creation_date)
                if creation_date is None:
                    continue
                # Включаем PR, если они пересекаются с диапазоном
                close_date = _safe_dt(pr.closed_date)
                pr_end = close_date or creation_date
                if pr_end < self.config.date_from:
                    continue
                if creation_date > self.config.date_to:
                    continue
                results.append(pr)

            if len(prs) < page_size:
                break
            skip += page_size

        return results

    def _matches_member(self, name: str, email: str) -> bool:
        name_lower = name.lower()
        email_lower = email.lower()
        for member in self.config.team_members:
            m = member.lower()
            if m in name_lower or m in email_lower or name_lower in m or email_lower == m:
                return True
        return False
