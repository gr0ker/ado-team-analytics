"""
Сборщик данных о Pull Request'ах из Azure DevOps.
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


class PullRequestsCollector(BaseCollector):
    """Собирает Pull Request'ы из всех репозиториев заданных проектов."""

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
                skip = 0
                page_size = 100
                while True:
                    try:
                        prs = self.git_client.get_pull_requests(
                            repository_id=repo.id,
                            search_criteria=search_criteria,
                            project=project,
                            top=page_size,
                            skip=skip,
                        )
                    except Exception as exc:
                        print(
                            f"  [WARN] Не удалось получить PR репозитория "
                            f"'{repo.name}' (проект '{project}'): {exc}"
                        )
                        break

                    if not prs:
                        break

                    for pr in prs:
                        creation_date = _safe_dt(pr.creation_date)
                        if creation_date is None:
                            continue

                        # Фильтрация по дате
                        if creation_date < self.config.date_from or creation_date > self.config.date_to:
                            # Если PR созданы раньше диапазона — дальнейшие тоже раньше (они сортированы по убыванию)
                            if creation_date < self.config.date_from:
                                prs = []  # прерываем внешний цикл
                            continue

                        created_by = pr.created_by
                        if created_by is None:
                            continue

                        author_name: str = created_by.display_name or ""
                        author_email: str = (created_by.unique_name or "").lower()

                        if self.config.team_members and not self._matches_member(
                            author_name, author_email
                        ):
                            continue

                        status_str = (pr.status or "").lower()
                        is_merged = status_str == "completed" and pr.merge_status == "succeeded"
                        close_date = _safe_dt(pr.closed_date)

                        results.append(
                            {
                                "author_name": author_name,
                                "author_email": author_email,
                                "date": creation_date,
                                "week": _week_label(creation_date),
                                "project": project,
                                "repo": repo.name,
                                "pr_id": pr.pull_request_id,
                                "title": pr.title or "",
                                "status": status_str,
                                "is_merged": is_merged,
                                "close_date": close_date,
                            }
                        )

                    if len(prs) < page_size:
                        break
                    skip += page_size

        return results

    # ------------------------------------------------------------------

    def _matches_member(self, name: str, email: str) -> bool:
        name_lower = name.lower()
        email_lower = email.lower()
        for member in self.config.team_members:
            m = member.lower()
            if m in name_lower or m in email_lower or name_lower in m or email_lower == m:
                return True
        return False
