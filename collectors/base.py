"""
Базовый класс для всех коллекторов данных из Azure DevOps.
"""
from __future__ import annotations

import urllib3
import warnings
from functools import cached_property

import requests
from msrest.authentication import BasicAuthentication
from azure.devops.connection import Connection

from config import AppConfig


class _SSLAwareBasicAuthentication(BasicAuthentication):
    """BasicAuthentication with configurable SSL certificate verification.

    :param ssl_verify: Passed to ``requests.Session.verify``.
        ``True``  — verify using the default CA bundle (default).
        ``False`` — disable verification (suppresses urllib3 warnings).
        ``str``   — path to a custom CA bundle or directory.
    """

    def __init__(self, username: str, password: str, ssl_verify: bool | str = True) -> None:
        super().__init__(username, password)
        self._ssl_verify = ssl_verify

    def signed_session(self, session: requests.Session | None = None) -> requests.Session:
        session = super().signed_session(session)
        session.verify = self._ssl_verify
        return session


class BaseCollector:
    """Базовый класс: создаёт подключение к Azure DevOps и предоставляет клиентов API."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        if config.ssl_verify is False:
            warnings.filterwarnings("ignore", category=urllib3.exceptions.InsecureRequestWarning)
        credentials = _SSLAwareBasicAuthentication("", config.pat, ssl_verify=config.ssl_verify)
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
