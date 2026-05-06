"""
Базовый класс для всех коллекторов данных из Azure DevOps.
"""
from __future__ import annotations

from functools import cached_property

from msrest.authentication import BasicAuthentication
from azure.devops.connection import Connection

from config import AppConfig


class BaseCollector:
    """Базовый класс: создаёт подключение к Azure DevOps и предоставляет клиентов API."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        credentials = BasicAuthentication("", config.pat)
        self.connection = Connection(base_url=config.server_url, creds=credentials)
        # Выбираем версию клиентов по настройке
        self._clients = (
            self.connection.clients_v7_0
            if config.api_version == "7.0"
            else self.connection.clients_v7_1
        )

    # ------------------------------------------------------------------
    # Ленивые клиенты API (создаются при первом обращении)
    # ------------------------------------------------------------------

    @cached_property
    def git_client(self):
        """Клиент Git API (репозитории, коммиты, PR, ревью)."""
        return self._clients.get_git_client()

    @cached_property
    def wit_client(self):
        """Клиент Work Item Tracking API."""
        return self._clients.get_work_item_tracking_client()

    @cached_property
    def core_client(self):
        """Клиент Core API (проекты, команды)."""
        return self._clients.get_core_client()
