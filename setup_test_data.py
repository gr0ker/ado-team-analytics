"""
Скрипт создания тестовых данных в Azure DevOps для проверки аналитики.

Создаёт:
  - Проект "TeamProductivityTest" (или использует существующий)
  - Git-репозиторий "test-repo"
  - Коммиты от 3 авторов за последние ~45 дней
  - Pull Requests с ветками feature/*
  - Ревью (голосования) на PR
  - Рабочие элементы: 3 User Story, 5 Task, 3 Bug

Запуск:
    python setup_test_data.py
"""
from __future__ import annotations

import base64
import os
import random
import sys
import time
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
from msrest.authentication import BasicAuthentication
from azure.devops.connection import Connection
from azure.devops.v7_1.core.models import TeamProject
from azure.devops.v7_1.git.models import (
    GitPush, GitRefUpdate, GitCommitRef, Change, ItemContent,
    GitRepository, GitPullRequest, GitPullRequestSearchCriteria,
    ResourceRef,
)
from azure.devops.v7_1.work_item_tracking.models import JsonPatchOperation

load_dotenv()

# ──────────────────────────────────────────────
# Конфигурация
# ──────────────────────────────────────────────
SERVER_URL = os.getenv("ADO_SERVER_URL", "").strip()
PAT = os.getenv("ADO_PAT", "").strip()
PROJECT_NAME = "TeamProductivityTest"

if not SERVER_URL or not PAT:
    print("❌ Задайте ADO_SERVER_URL и ADO_PAT в файле .env")
    sys.exit(1)

# Три фиктивных автора (имя + email)
AUTHORS = [
    {"name": "Иван Иванов", "email": "ivan@example.com"},
    {"name": "Мария Петрова", "email": "maria@example.com"},
    {"name": "Алексей Сидоров", "email": "alexey@example.com"},
]

NOW = datetime.now(timezone.utc)


# ──────────────────────────────────────────────
# Подключение
# ──────────────────────────────────────────────
credentials = BasicAuthentication("", PAT)
connection = Connection(base_url=SERVER_URL, creds=credentials)

core_client = connection.clients_v7_1.get_core_client()
git_client = connection.clients_v7_1.get_git_client()
wit_client = connection.clients_v7_1.get_work_item_tracking_client()


def _get_current_user_id() -> str:
    """Возвращает ID текущего пользователя через connectionData API."""
    import requests as _requests
    url = SERVER_URL.rstrip("/") + "/_apis/connectionData"
    resp = _requests.get(
        url,
        headers={"Authorization": "Basic " + base64.b64encode((":" + PAT).encode()).decode()},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()["authenticatedUser"]["id"]


try:
    CURRENT_USER_ID = _get_current_user_id()
except Exception as _e:
    print(f"  ⚠️  Не удалось получить ID текущего пользователя: {_e}")
    CURRENT_USER_ID = None


# ──────────────────────────────────────────────
# Утилиты
# ──────────────────────────────────────────────

def _b64(text: str) -> str:
    return base64.b64encode(text.encode()).decode()


def _days_ago(n: int) -> datetime:
    return NOW - timedelta(days=n)


def _pause(sec: float = 0.5) -> None:
    time.sleep(sec)


# ──────────────────────────────────────────────
# Шаг 1: Проект
# ──────────────────────────────────────────────

def ensure_project() -> str:
    """Возвращает ID проекта, создаёт если не существует."""
    print(f"\n[1] Проверяем проект '{PROJECT_NAME}'...")
    projects = core_client.get_projects()
    for p in projects:
        if p.name == PROJECT_NAME:
            print(f"    ✓ Проект уже существует (id={p.id})")
            return p.id

    print(f"    Создаём проект '{PROJECT_NAME}'...")
    project = TeamProject(
        name=PROJECT_NAME,
        description="Тестовый проект для аналитики продуктивности команды",
        visibility="private",
        capabilities={
            "versioncontrol": {"sourceControlType": "Git"},
            "processTemplate": {"templateTypeId": "6b724908-ef14-45cf-84f8-768b5384da45"},
        },
    )
    operation = core_client.queue_create_project(project)
    print(f"    Операция поставлена в очередь. Ждём завершения...")

    # Ждём создания проекта (до 2 минут)
    for _ in range(24):
        _pause(5)
        projects = core_client.get_projects()
        for p in projects:
            if p.name == PROJECT_NAME:
                print(f"    ✓ Проект создан (id={p.id})")
                return p.id
    raise RuntimeError("Проект не был создан за отведённое время.")


# ──────────────────────────────────────────────
# Шаг 2: Репозиторий
# ──────────────────────────────────────────────

def ensure_repo(project_id: str) -> GitRepository:
    """Возвращает репозиторий 'test-repo', создаёт если нет."""
    print(f"\n[2] Проверяем репозиторий 'test-repo'...")
    repos = git_client.get_repositories(project=project_id)
    for r in repos:
        if r.name == "test-repo":
            print(f"    ✓ Репозиторий уже существует")
            return r

    print(f"    Создаём репозиторий 'test-repo'...")
    repo = git_client.create_repository(
        git_repository_to_create={"name": "test-repo", "project": {"id": project_id}},
        project=project_id,
    )
    print(f"    ✓ Репозиторий создан (id={repo.id})")
    _pause(2)
    return repo


# ──────────────────────────────────────────────
# Шаг 3: Коммиты
# ──────────────────────────────────────────────

def _get_default_branch_ref(repo_id: str, project: str) -> str:
    """Возвращает текущий SHA HEAD ветки main/master."""
    try:
        refs = git_client.get_refs(repo_id, project=project, filter="heads/main")
        if refs:
            return refs[0].object_id
    except Exception:
        pass
    try:
        refs = git_client.get_refs(repo_id, project=project, filter="heads/master")
        if refs:
            return refs[0].object_id
    except Exception:
        pass
    return "0000000000000000000000000000000000000000"


def _get_ref_sha(repo_id: str, project: str, ref_name: str) -> str:
    try:
        refs = git_client.get_refs(repo_id, project=project, filter=f"heads/{ref_name}")
        if refs:
            return refs[0].object_id
    except Exception:
        pass
    return "0000000000000000000000000000000000000000"


def create_initial_commit(repo: GitRepository, project: str) -> str:
    """Создаёт первый коммит в main (README.md)."""
    print("\n[3] Создаём начальный коммит в main...")

    readme_content = (
        "# TeamProductivityTest\n\n"
        "Тестовый репозиторий для аналитики продуктивности команды.\n"
    )

    push = GitPush(
        ref_updates=[
            GitRefUpdate(
                name="refs/heads/main",
                old_object_id="0000000000000000000000000000000000000000",
            )
        ],
        commits=[
            GitCommitRef(
                comment="Initial commit: add README",
                author={"name": "Setup Script", "email": "setup@example.com",
                        "date": NOW.isoformat()},
                committer={"name": "Setup Script", "email": "setup@example.com",
                           "date": NOW.isoformat()},
                changes=[
                    Change(
                        change_type="add",
                        item={"path": "/README.md"},
                        new_content=ItemContent(
                            content=_b64(readme_content),
                            content_type="base64Encoded",
                        ),
                    )
                ],
            )
        ],
    )
    try:
        result = git_client.create_push(push, repo.id, project=project)
        sha = result.ref_updates[0].new_object_id
        print(f"    ✓ Начальный коммит создан (SHA: {sha[:8]}...)")
        _pause(1)
        return sha
    except Exception as exc:
        print(f"    ⚠️  Начальный коммит уже существует или ошибка: {exc}")
        return _get_ref_sha(repo.id, project, "main")


def create_author_commits(
    repo: GitRepository,
    project: str,
    branch: str,
    author: dict,
    base_sha: str,
    commit_count: int = 5,
    days_range: tuple = (5, 45),
) -> str:
    """Создаёт несколько коммитов от одного автора в указанной ветке."""
    current_sha = _get_ref_sha(repo.id, project, branch)
    is_new_branch = current_sha == "0000000000000000000000000000000000000000"

    print(f"    Создаём {commit_count} коммитов от '{author['name']}' в ветке '{branch}'...")

    for i in range(commit_count):
        days_offset = random.randint(*days_range)
        commit_date = (NOW - timedelta(days=days_offset)).isoformat()
        file_name = f"src/{author['name'].replace(' ', '_')}_file_{i + 1}.py"
        file_content = (
            f"# Файл создан {author['name']}\n"
            f"# Дата: {commit_date[:10]}\n\n"
            f"def feature_{i + 1}():\n"
            f"    \"\"\"Функция номер {i + 1}.\"\"\"\n"
            f"    return {random.randint(1, 100)}\n"
        )

        ref_update = GitRefUpdate(
            name=f"refs/heads/{branch}",
            old_object_id=current_sha if not is_new_branch else base_sha
            if i == 0
            else current_sha,
        )
        if is_new_branch and i == 0:
            ref_update.old_object_id = "0000000000000000000000000000000000000000"
            # Для новой ветки нужно указать базовый SHA
            # Azure DevOps не поддерживает напрямую создание ветки с base,
            # поэтому создаём через refUpdate с baseObjectId
            # Используем workaround: сначала создаём ветку

        push = GitPush(
            ref_updates=[
                GitRefUpdate(
                    name=f"refs/heads/{branch}",
                    old_object_id=current_sha,
                )
            ],
            commits=[
                GitCommitRef(
                    comment=f"feat: {author['name']} — изменение {i + 1}",
                    author={
                        "name": author["name"],
                        "email": author["email"],
                        "date": commit_date,
                    },
                    committer={
                        "name": author["name"],
                        "email": author["email"],
                        "date": commit_date,
                    },
                    changes=[
                        Change(
                            change_type="add" if i == 0 else "edit",
                            item={"path": f"/{file_name}"},
                            new_content=ItemContent(
                                content=_b64(file_content),
                                content_type="base64Encoded",
                            ),
                        )
                    ],
                )
            ],
        )

        try:
            result = git_client.create_push(push, repo.id, project=project)
            current_sha = result.ref_updates[0].new_object_id
            is_new_branch = False
            _pause(0.3)
        except Exception as exc:
            print(f"      ⚠️  Коммит {i + 1} не создан: {exc}")
            current_sha = _get_ref_sha(repo.id, project, branch)
            if current_sha == "0000000000000000000000000000000000000000":
                break

    print(f"    ✓ Коммиты в ветке '{branch}' готовы")
    return current_sha


def create_all_commits(repo: GitRepository, project: str) -> None:
    """Создаёт ветки и коммиты для всех авторов."""
    print("\n[3] Создаём коммиты от трёх авторов...")

    # Убеждаемся, что main существует
    main_sha = _get_ref_sha(repo.id, project, "main")
    if main_sha == "0000000000000000000000000000000000000000":
        main_sha = create_initial_commit(repo, project)

    branches = [
        ("feature/ivan-feature-1", AUTHORS[0], 6, (5, 40)),
        ("feature/ivan-feature-2", AUTHORS[0], 4, (2, 20)),
        ("feature/maria-feature-1", AUTHORS[1], 7, (3, 45)),
        ("feature/maria-bugfix-1", AUTHORS[1], 3, (1, 15)),
        ("feature/alexey-feature-1", AUTHORS[2], 5, (10, 50)),
        ("feature/alexey-refactor", AUTHORS[2], 4, (5, 30)),
    ]

    for branch_name, author, count, days_range in branches:
        # Создаём ветку от main
        branch_sha = _get_ref_sha(repo.id, project, branch_name)
        branch_is_new = branch_sha == "0000000000000000000000000000000000000000"

        if branch_is_new:
            # Создаём ветку через push с первым коммитом
            file_content = f"# Ветка {branch_name}\n# Автор: {author['name']}\n"
            push = GitPush(
                ref_updates=[
                    GitRefUpdate(
                        name=f"refs/heads/{branch_name}",
                        old_object_id="0000000000000000000000000000000000000000",
                    )
                ],
                commits=[
                    GitCommitRef(
                        comment=f"branch: создание ветки {branch_name}",
                        author={
                            "name": author["name"],
                            "email": author["email"],
                            "date": (NOW - timedelta(days=days_range[1] + 1)).isoformat(),
                        },
                        committer={
                            "name": author["name"],
                            "email": author["email"],
                            "date": (NOW - timedelta(days=days_range[1] + 1)).isoformat(),
                        },
                        changes=[
                            Change(
                                change_type="add",
                                item={"path": f"/branches/{branch_name.replace('/', '_')}.md"},
                                new_content=ItemContent(
                                    content=_b64(file_content),
                                    content_type="base64Encoded",
                                ),
                            )
                        ],
                    )
                ],
            )
            try:
                result = git_client.create_push(push, repo.id, project=project)
                branch_sha = result.ref_updates[0].new_object_id
                _pause(0.5)
            except Exception as exc:
                print(f"    ⚠️  Ветка '{branch_name}' не создана: {exc}")
                branch_sha = _get_ref_sha(repo.id, project, branch_name)
                if branch_sha == "0000000000000000000000000000000000000000":
                    continue

        # Добавляем коммиты в ветку
        # Если ветка уже существовала — файлы уже есть, используем edit; иначе add
        change_type = "add" if branch_is_new else "edit"
        for i in range(count - 1):
            days_offset = random.randint(*days_range)
            commit_date = (NOW - timedelta(days=days_offset)).isoformat()
            file_name = f"src/{branch_name.replace('/', '_')}_change_{i + 1}.py"
            file_content = (
                f"# Автор: {author['name']}\n"
                f"# Дата: {commit_date[:10]}\n\n"
                f"CONSTANT_{i + 1} = {random.randint(100, 999)}\n\n"
                f"def compute_{i + 1}(x):\n    return x * CONSTANT_{i + 1}\n"
            )
            current_sha = _get_ref_sha(repo.id, project, branch_name)
            if current_sha == "0000000000000000000000000000000000000000":
                break

            push = GitPush(
                ref_updates=[
                    GitRefUpdate(
                        name=f"refs/heads/{branch_name}",
                        old_object_id=current_sha,
                    )
                ],
                commits=[
                    GitCommitRef(
                        comment=f"feat({branch_name.split('/')[-1]}): изменение #{i + 2}",
                        author={"name": author["name"], "email": author["email"],
                                "date": commit_date},
                        committer={"name": author["name"], "email": author["email"],
                                   "date": commit_date},
                        changes=[
                            Change(
                                change_type=change_type,
                                item={"path": f"/{file_name}"},
                                new_content=ItemContent(
                                    content=_b64(file_content),
                                    content_type="base64Encoded",
                                ),
                            )
                        ],
                    )
                ],
            )
            try:
                result = git_client.create_push(push, repo.id, project=project)
                _pause(0.3)
            except Exception as exc:
                print(f"      ⚠️  Коммит в {branch_name}: {exc}")

        print(f"    ✓ Ветка '{branch_name}' готова")


# ──────────────────────────────────────────────
# Шаг 4: Pull Requests
# ──────────────────────────────────────────────

def create_pull_requests(repo: GitRepository, project: str) -> list:
    """Создаёт Pull Requests из feature-веток в main."""
    print("\n[4] Создаём Pull Requests...")

    # Получаем существующие ветки
    try:
        refs = git_client.get_refs(repo.id, project=project, filter="heads/feature/")
    except Exception as exc:
        print(f"    ⚠️  Не удалось получить ветки: {exc}")
        return []

    pr_specs = [
        {
            "branch": "feature/ivan-feature-1",
            "title": "Иван: новая функциональность #1",
            "description": "Добавляет новые вычислительные функции.",
            "complete": True,
        },
        {
            "branch": "feature/maria-feature-1",
            "title": "Мария: основная функциональность",
            "description": "Реализация основных функций проекта.",
            "complete": True,
        },
        {
            "branch": "feature/maria-bugfix-1",
            "title": "Мария: исправление ошибки #1",
            "description": "Исправляет критическую ошибку в вычислениях.",
            "complete": False,
        },
        {
            "branch": "feature/alexey-feature-1",
            "title": "Алексей: новый модуль",
            "description": "Добавляет новый вспомогательный модуль.",
            "complete": False,
        },
        {
            "branch": "feature/ivan-feature-2",
            "title": "Иван: доработка функциональности",
            "description": "Улучшение производительности.",
            "complete": False,
        },
    ]

    branch_names = {r.name.replace("refs/heads/", "") for r in refs}
    created_prs = []

    for spec in pr_specs:
        branch = spec["branch"]
        if branch not in branch_names:
            print(f"    ⚠️  Ветка '{branch}' не найдена, пропускаем PR")
            continue

        # Проверяем, нет ли уже PR для этой ветки
        try:
            existing = git_client.get_pull_requests(
                repository_id=repo.id,
                search_criteria=GitPullRequestSearchCriteria(
                    source_ref_name=f"refs/heads/{branch}",
                    status="all",
                ),
                project=project,
            )
            if existing:
                print(f"    ✓ PR из '{branch}' уже существует")
                created_prs.append(existing[0])
                continue
        except Exception:
            pass

        try:
            pr = git_client.create_pull_request(
                git_pull_request_to_create=GitPullRequest(
                    title=spec["title"],
                    description=spec["description"],
                    source_ref_name=f"refs/heads/{branch}",
                    target_ref_name="refs/heads/main",
                ),
                repository_id=repo.id,
                project=project,
            )
            print(f"    ✓ PR создан: '{spec['title']}' (#{pr.pull_request_id})")
            created_prs.append(pr)
            _pause(0.5)
        except Exception as exc:
            print(f"    ⚠️  Не удалось создать PR из '{branch}': {exc}")

    return created_prs


# ──────────────────────────────────────────────
# Шаг 5: Ревью
# ──────────────────────────────────────────────

def add_reviews(repo: GitRepository, project: str, prs: list) -> None:
    """Добавляет голоса и комментарии к PR."""
    print("\n[5] Добавляем ревью и комментарии...")

    vote_options = [10, 5, 0]  # approved, suggestions, no vote

    for pr in prs:
        pr_id = pr.pull_request_id

        # Добавляем голос (от текущего пользователя)
        vote = random.choice(vote_options)
        if CURRENT_USER_ID:
            try:
                git_client.create_pull_request_reviewer(
                    reviewer={"vote": vote, "isRequired": False},
                    repository_id=repo.id,
                    pull_request_id=pr_id,
                    reviewer_id=CURRENT_USER_ID,
                    project=project,
                )
                vote_label = {10: "approved", 5: "suggestions", 0: "no vote"}[vote]
                print(f"    ✓ Голос '{vote_label}' добавлен к PR#{pr_id}")
            except Exception as exc:
                print(f"    ⚠️  Ревью PR#{pr_id}: {exc}")
        else:
            print(f"    ⚠️  Пропускаем ревью PR#{pr_id}: ID пользователя недоступен")

        # Добавляем комментарий
        try:
            git_client.create_thread(
                comment_thread={
                    "comments": [
                        {
                            "parentCommentId": 0,
                            "content": f"Ревью кода PR#{pr_id}: выглядит хорошо, но стоит добавить тесты.",
                            "commentType": 1,
                        }
                    ],
                    "status": 1,
                },
                repository_id=repo.id,
                pull_request_id=pr_id,
                project=project,
            )
            print(f"    ✓ Комментарий добавлен к PR#{pr_id}")
        except Exception as exc:
            print(f"    ⚠️  Комментарий PR#{pr_id}: {exc}")

        _pause(0.3)


# ──────────────────────────────────────────────
# Шаг 6: Work Items
# ──────────────────────────────────────────────

def create_work_items(project: str) -> None:
    """Создаёт рабочие элементы для тестирования."""
    print("\n[6] Создаём рабочие элементы...")

    items = [
        # Product Backlog Items (Scrum template)
        {
            "type": "Product Backlog Item",
            "title": "Аналитика коммитов по участникам",
            "state": "New",
        },
        {
            "type": "Product Backlog Item",
            "title": "Дашборд продуктивности команды",
            "state": "Committed",
        },
        {
            "type": "Product Backlog Item",
            "title": "Экспорт отчётов в Excel",
            "state": "Done",
        },
        # Tasks
        {
            "type": "Task",
            "title": "Настроить сбор данных коммитов",
            "state": "Closed",
        },
        {
            "type": "Task",
            "title": "Реализовать HTML-отчёт",
            "state": "Closed",
        },
        {
            "type": "Task",
            "title": "Написать документацию",
            "state": "Active",
        },
        {
            "type": "Task",
            "title": "Добавить тесты для коллекторов",
            "state": "New",
        },
        {
            "type": "Task",
            "title": "Оптимизировать WIQL-запросы",
            "state": "Active",
        },
        # Bugs
        {
            "type": "Bug",
            "title": "Неверная дата в отчёте при UTC+3",
            "state": "Active",
        },
        {
            "type": "Bug",
            "title": "PR с пустым автором вызывает ошибку",
            "state": "Resolved",
        },
        {
            "type": "Bug",
            "title": "Пагинация пропускает последний элемент",
            "state": "Closed",
        },
    ]

    for item in items:
        ops = [
            JsonPatchOperation(
                op="add",
                path="/fields/System.Title",
                value=item["title"],
            ),
        ]
        try:
            wi = wit_client.create_work_item(
                document=ops,
                project=project,
                type=item["type"],
            )
            print(f"    ✓ {item['type']}: '{item['title']}' (#{wi.id}) [{item['state']}]")
            _pause(0.2)
        except Exception as exc:
            print(f"    ⚠️  {item['type']} '{item['title']}': {exc}")


# ──────────────────────────────────────────────
# Главная функция
# ──────────────────────────────────────────────

def main() -> None:
    print("=" * 60)
    print("  Создание тестовых данных в Azure DevOps")
    print("=" * 60)
    print(f"  Сервер:  {SERVER_URL}")
    print(f"  Проект:  {PROJECT_NAME}")
    print("=" * 60)

    # 1. Проект
    project_id = ensure_project()

    # 2. Репозиторий
    repo = ensure_repo(project_id)

    # 3. Коммиты
    create_all_commits(repo, PROJECT_NAME)

    # 4. Pull Requests
    prs = create_pull_requests(repo, PROJECT_NAME)

    # 5. Ревью
    if prs:
        add_reviews(repo, PROJECT_NAME, prs)

    # 6. Work Items
    create_work_items(PROJECT_NAME)

    print("\n" + "=" * 60)
    print("  ✅ Тестовые данные успешно созданы!")
    print()
    print("  Теперь добавьте в .env:")
    print(f"    ADO_PROJECTS={PROJECT_NAME}")
    print(f"    ADO_TEAM_MEMBERS=ivan@example.com,maria@example.com,alexey@example.com")
    print(f"    DATE_FROM={(NOW - timedelta(days=60)).strftime('%Y-%m-%d')}")
    print(f"    DATE_TO={NOW.strftime('%Y-%m-%d')}")
    print()
    print("  И запустите:")
    print("    python main.py")
    print("=" * 60)


if __name__ == "__main__":
    main()

