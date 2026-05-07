"""
Загрузка и валидация конфигурации из .env файла.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import List, Optional

from dotenv import load_dotenv


class AppConfig:
    """Конфигурация приложения, загружаемая из переменных окружения."""

    def __init__(self, env_file: str = ".env"):
        load_dotenv(env_file, override=True)

        self.server_url: str = self._require("ADO_SERVER_URL")
        self.pat: str = self._require("ADO_PAT")

        projects_raw = self._require("ADO_PROJECTS")
        self.projects: List[str] = [p.strip() for p in projects_raw.split(",") if p.strip()]
        if not self.projects:
            raise ValueError("ADO_PROJECTS не может быть пустым — укажите хотя бы один проект.")

        members_raw = os.getenv("ADO_TEAM_MEMBERS", "").strip()
        self.team_members: List[str] = (
            [m.strip() for m in members_raw.split(",") if m.strip()]
            if members_raw
            else []
        )

        api_version_raw = os.getenv("ADO_API_VERSION", "7.1").strip()
        if api_version_raw not in ("7.0", "7.1"):
            raise ValueError(
                f"ADO_API_VERSION='{api_version_raw}' не поддерживается. "
                f"Допустимые значения: 7.0, 7.1"
            )
        self.api_version: str = api_version_raw

        ssl_verify_raw = os.getenv("ADO_SSL_VERIFY", "true").strip()
        if ssl_verify_raw.lower() == "false":
            self.ssl_verify: bool | str = False
        elif ssl_verify_raw.lower() == "true":
            self.ssl_verify = True
        else:
            # Treat as path to a custom CA bundle file
            self.ssl_verify = ssl_verify_raw

        date_from_raw = self._require("DATE_FROM")
        date_to_raw = self._require("DATE_TO")
        self.date_from: datetime = self._parse_date(date_from_raw, "DATE_FROM")
        self.date_to: datetime = self._parse_date(date_to_raw, "DATE_TO")

        if self.date_from > self.date_to:
            raise ValueError(
                f"DATE_FROM ({date_from_raw}) не может быть позже DATE_TO ({date_to_raw})."
            )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _require(key: str) -> str:
        value = os.getenv(key, "").strip()
        if not value:
            raise ValueError(
                f"Обязательная переменная окружения '{key}' не задана или пуста. "
                f"Проверьте файл .env (см. .env.example для примера)."
            )
        return value

    @staticmethod
    def _parse_date(value: str, key: str) -> datetime:
        for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%Y/%m/%d"):
            try:
                dt = datetime.strptime(value, fmt)
                return dt.replace(tzinfo=timezone.utc)
            except ValueError:
                continue
        raise ValueError(
            f"Не удалось разобрать дату из '{key}={value}'. "
            f"Используйте формат YYYY-MM-DD, например: 2024-01-01."
        )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"AppConfig(server_url={self.server_url!r}, projects={self.projects}, "
            f"date_from={self.date_from.date()}, date_to={self.date_to.date()}, "
            f"team_members={self.team_members}, ssl_verify={self.ssl_verify!r})"
        )
