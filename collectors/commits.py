"""
Сборщик данных о коммитах из всех репозиториев Azure DevOps.
"""
from __future__ import annotations

from datetime import datetime
from typing import List, Dict, Any

from azure.devops.v7_1.git.models import GitQueryCommitsCriteria

from collectors.base import BaseCollector


def _week_label(dt: datetime) -> str:
    """Возвращает метку недели вида 'YYYY-WNN'."""
    return f"{dt.isocalendar()[0]}-W{dt.isocalendar()[1]:02d}"


class CommitsCollector(BaseCollector):
    """Собирает коммиты из всех репозиториев заданных проектов."""

    def collect(self) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []

        criteria = GitQueryCommitsCriteria(
            from_date=self.config.date_from.strftime("%m/%d/%Y %H:%M:%S"),
            to_date=self.config.date_to.strftime("%m/%d/%Y %H:%M:%S"),
        )

        for project in self.config.projects:
            try:
                repos = self.git_client.get_repositories(project=project)
            except Exception as exc:
                print(f"  [WARN] Не удалось получить репозитории проекта '{project}': {exc}")
                continue

            for repo in repos:
                try:
                    commits = self.git_client.get_commits(
                        repository_id=repo.id,
                        search_criteria=criteria,
                        project=project,
                        top=10000,
                    )
                except Exception as exc:
                    print(
                        f"  [WARN] Не удалось получить коммиты репозитория "
                        f"'{repo.name}' (проект '{project}'): {exc}"
                    )
                    continue

                for commit in commits:
                    author = commit.author
                    if author is None:
                        continue

                    author_name: str = author.name or ""
                    author_email: str = (author.email or "").lower()

                    if self.config.team_members and not self._matches_member(
                        author_name, author_email
                    ):
                        continue

                    commit_date: datetime = author.date if isinstance(author.date, datetime) else datetime.fromisoformat(str(author.date))

                    change_counts = commit.change_counts or {}
                    additions = (change_counts.get("Add", 0) or 0) + (change_counts.get("Edit", 0) or 0)
                    deletions = change_counts.get("Delete", 0) or 0

                    results.append(
                        {
                            "author_name": author_name,
                            "author_email": author_email,
                            "date": commit_date,
                            "week": _week_label(commit_date),
                            "project": project,
                            "repo": repo.name,
                            "additions": additions,
                            "deletions": deletions,
                        }
                    )

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
