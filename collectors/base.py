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


class _SSLVerifyAdapter(requests.adapters.HTTPAdapter):
    """HTTPAdapter that enforces a fixed SSL verification setting.

    ``msrest``'s ``RequestsHTTPSender`` passes an explicit ``verify`` kwarg
    (sourced from ``ClientConnection.__call__``) to every ``session.request()``
    call.  Because explicit kwargs override ``session.verify``, setting only
    ``session.verify`` is not enough.  Mounting this adapter ensures the
    correct value reaches ``urllib3`` regardless of what the caller passes.
    """

    def __init__(self, ssl_verify: bool | str, **kwargs) -> None:
        self._ssl_verify = ssl_verify
        super().__init__(**kwargs)

    def send(self, request, **kwargs):
        kwargs["verify"] = self._ssl_verify
        return super().send(request, **kwargs)


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
        if self._ssl_verify is not True:
            # Mount a custom adapter so the verify value is enforced even when
            # msrest passes it as an explicit kwarg that would otherwise
            # override session.verify.
            adapter = _SSLVerifyAdapter(self._ssl_verify)
            session.mount("https://", adapter)
            session.mount("http://", adapter)
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
