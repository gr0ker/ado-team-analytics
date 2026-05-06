"""
Сборщик данных о рабочих элементах (Work Items) из Azure DevOps.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from azure.devops.v7_1.work_item_tracking.models import Wiql, TeamContext

from collectors.base import BaseCollector


def _week_label(dt: datetime) -> str:
    return f"{dt.isocalendar()[0]}-W{dt.isocalendar()[1]:02d}"


def _safe_dt(value) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    try:
        s = str(value)
        # ADO возвращает строки вида "2024-01-15T10:23:00Z"
        s = s.replace("Z", "+00:00")
        return datetime.fromisoformat(s)
    except Exception:
        return None


_FIELDS = [
    "System.Id",
    "System.Title",
    "System.State",
    "System.AssignedTo",
    "System.CreatedBy",
    "System.CreatedDate",
    "System.ChangedDate",
    "System.WorkItemType",
    "Microsoft.VSTS.Common.ClosedDate",
]


class WorkItemsCollector(BaseCollector):
    """Собирает рабочие элементы (задачи, баги, истории) из заданных проектов."""

    def collect(self) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []

        date_from_str = self.config.date_from.strftime("%Y-%m-%d")
        date_to_str = self.config.date_to.strftime("%Y-%m-%d")

        for project in self.config.projects:
            # 1. Созданные в диапазоне
            created_ids = self._run_wiql(
                project,
                f"""
                SELECT [System.Id]
                FROM WorkItems
                WHERE [System.TeamProject] = '{project}'
                  AND [System.CreatedDate] >= '{date_from_str}'
                  AND [System.CreatedDate] <= '{date_to_str}'
                ORDER BY [System.CreatedDate] DESC
                """,
            )

            # 2. Закрытые/решённые в диапазоне
            closed_ids = self._run_wiql(
                project,
                f"""
                SELECT [System.Id]
                FROM WorkItems
                WHERE [System.TeamProject] = '{project}'
                  AND [System.State] IN ('Closed', 'Resolved', 'Done')
                  AND [Microsoft.VSTS.Common.ClosedDate] >= '{date_from_str}'
                  AND [Microsoft.VSTS.Common.ClosedDate] <= '{date_to_str}'
                ORDER BY [Microsoft.VSTS.Common.ClosedDate] DESC
                """,
            )

            # 3. Назначенные на участников команды (если список задан)
            assigned_ids: List[int] = []
            if self.config.team_members:
                for member in self.config.team_members:
                    ids = self._run_wiql(
                        project,
                        f"""
                        SELECT [System.Id]
                        FROM WorkItems
                        WHERE [System.TeamProject] = '{project}'
                          AND [System.AssignedTo] CONTAINS '{member}'
                          AND [System.ChangedDate] >= '{date_from_str}'
                          AND [System.ChangedDate] <= '{date_to_str}'
                        ORDER BY [System.ChangedDate] DESC
                        """,
                    )
                    assigned_ids.extend(ids)

            # Загружаем детали
            all_ids = list(set(created_ids + closed_ids + assigned_ids))
            if not all_ids:
                continue

            items_map = self._fetch_items(project, all_ids)

            for wi_id, fields in items_map.items():
                state = fields.get("System.State", "")
                wi_type = fields.get("System.WorkItemType", "")
                title = fields.get("System.Title", "")
                created_date = _safe_dt(fields.get("System.CreatedDate"))
                closed_date = _safe_dt(fields.get("Microsoft.VSTS.Common.ClosedDate"))

                # Извлекаем имя/email автора и назначенного
                assigned_to = fields.get("System.AssignedTo") or {}
                created_by = fields.get("System.CreatedBy") or {}

                def _person_name(val) -> str:
                    if isinstance(val, dict):
                        return val.get("displayName") or val.get("uniqueName") or ""
                    return str(val) if val else ""

                def _person_email(val) -> str:
                    if isinstance(val, dict):
                        return (val.get("uniqueName") or "").lower()
                    return ""

                # Запись: создан
                if wi_id in created_ids and created_date:
                    person_n = _person_name(created_by)
                    person_e = _person_email(created_by)
                    if not self.config.team_members or self._matches_member(person_n, person_e):
                        results.append(self._make_record(
                            "created", person_n, person_e, created_date,
                            project, wi_id, wi_type, state, title,
                        ))

                # Запись: закрыт
                if wi_id in closed_ids and closed_date:
                    person_n = _person_name(assigned_to) or _person_name(created_by)
                    person_e = _person_email(assigned_to) or _person_email(created_by)
                    if not self.config.team_members or self._matches_member(person_n, person_e):
                        results.append(self._make_record(
                            "closed", person_n, person_e, closed_date,
                            project, wi_id, wi_type, state, title,
                        ))

                # Запись: назначен
                if wi_id in assigned_ids:
                    ref_date = created_date or self.config.date_from
                    person_n = _person_name(assigned_to)
                    person_e = _person_email(assigned_to)
                    if not self.config.team_members or self._matches_member(person_n, person_e):
                        results.append(self._make_record(
                            "assigned", person_n, person_e, ref_date,
                            project, wi_id, wi_type, state, title,
                        ))

        return results

    # ------------------------------------------------------------------

    def _run_wiql(self, project: str, query: str) -> List[int]:
        try:
            wiql = Wiql(query=query)
            team_context = TeamContext(project=project)
            result = self.wit_client.query_by_wiql(wiql, team_context=team_context, top=10000)
            return [int(ref.id) for ref in (result.work_items or [])]
        except Exception as exc:
            print(f"  [WARN] WIQL запрос не выполнен (проект '{project}'): {exc}")
            return []

    def _fetch_items(self, project: str, ids: List[int]) -> Dict[int, Dict]:
        from azure.devops.v7_1.work_item_tracking.models import WorkItemBatchGetRequest
        result: Dict[int, Dict] = {}
        # Батчевый запрос по 200 элементов
        batch_size = 200
        for i in range(0, len(ids), batch_size):
            batch = ids[i: i + batch_size]
            try:
                request = WorkItemBatchGetRequest(ids=batch, fields=_FIELDS)
                items = self.wit_client.get_work_items_batch(request, project=project)
                for item in items:
                    result[item.id] = item.fields or {}
            except Exception as exc:
                print(f"  [WARN] Не удалось загрузить рабочие элементы (проект '{project}'): {exc}")
        return result

    @staticmethod
    def _make_record(
        record_type: str,
        person_name: str,
        person_email: str,
        date: datetime,
        project: str,
        wi_id: int,
        wi_type: str,
        wi_state: str,
        title: str,
    ) -> Dict[str, Any]:
        return {
            "record_type": record_type,
            "person_name": person_name,
            "person_email": person_email,
            "date": date,
            "week": _week_label(date),
            "project": project,
            "wi_id": wi_id,
            "wi_type": wi_type,
            "wi_state": wi_state,
            "title": title,
        }

    def _matches_member(self, name: str, email: str) -> bool:
        name_lower = name.lower()
        email_lower = email.lower()
        for member in self.config.team_members:
            m = member.lower()
            if m in name_lower or m in email_lower or name_lower in m or email_lower == m:
                return True
        return False
