"""
Точка входа: сбор данных из Azure DevOps и генерация отчётов.

Использование:
    python main.py --from 2024-01-01 --to 2024-03-31 --format both --output ./output
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime

from tabulate import tabulate


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Azure DevOps Team Productivity Analytics",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Примеры:
  python main.py --from 2024-01-01 --to 2024-03-31
  python main.py --from 2024-01-01 --to 2024-03-31 --format html --output ./reports
  python main.py --config /path/to/.env --format excel
        """,
    )
    parser.add_argument("--from", dest="date_from", metavar="YYYY-MM-DD",
                        help="Начало периода (переопределяет DATE_FROM в .env)")
    parser.add_argument("--to", dest="date_to", metavar="YYYY-MM-DD",
                        help="Конец периода (переопределяет DATE_TO в .env)")
    parser.add_argument("--format", choices=["html", "excel", "both"], default="both",
                        help="Формат отчёта (по умолчанию: both)")
    parser.add_argument("--output", default="./output",
                        help="Директория для сохранения отчётов (по умолчанию: ./output)")
    parser.add_argument("--config", default=".env",
                        help="Путь к файлу .env (по умолчанию: .env)")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()

    # Переопределяем переменные окружения до загрузки конфига
    if args.date_from:
        os.environ["DATE_FROM"] = args.date_from
    if args.date_to:
        os.environ["DATE_TO"] = args.date_to

    # --- Загрузка конфига ---
    print("⚙️  Загружаем конфигурацию...")
    try:
        from config import AppConfig
        config = AppConfig(env_file=args.config)
    except ValueError as exc:
        print(f"❌ Ошибка конфигурации: {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"   Сервер:    {config.server_url}")
    print(f"   Проекты:   {', '.join(config.projects)}")
    print(f"   Участники: {', '.join(config.team_members) if config.team_members else '(все авторы)'}")
    print()

    # --- Сбор данных ---
    print(f"📥 Собираем данные за период: "
          f"{config.date_from.strftime('%d.%m.%Y')} — {config.date_to.strftime('%d.%m.%Y')}")

    from collectors import (
        CommitsCollector,
        PullRequestsCollector,
        ReviewsCollector,
        WorkItemsCollector,
    )

    print("   [1/4] Коммиты...", end=" ", flush=True)
    try:
        commits = CommitsCollector(config).collect()
        print(f"✓  ({len(commits)} записей)")
    except Exception as exc:
        print(f"⚠️  Ошибка: {exc}")
        commits = []

    print("   [2/4] Pull Requests...", end=" ", flush=True)
    try:
        prs = PullRequestsCollector(config).collect()
        print(f"✓  ({len(prs)} записей)")
    except Exception as exc:
        print(f"⚠️  Ошибка: {exc}")
        prs = []

    print("   [3/4] Ревью и комментарии...", end=" ", flush=True)
    try:
        reviews = ReviewsCollector(config).collect()
        print(f"✓  ({len(reviews)} записей)")
    except Exception as exc:
        print(f"⚠️  Ошибка: {exc}")
        reviews = []

    print("   [4/4] Рабочие элементы...", end=" ", flush=True)
    try:
        work_items = WorkItemsCollector(config).collect()
        print(f"✓  ({len(work_items)} записей)")
    except Exception as exc:
        print(f"⚠️  Ошибка: {exc}")
        work_items = []

    print()

    # --- Агрегация ---
    print("📊 Агрегируем данные...", end=" ", flush=True)
    from aggregator import DataAggregator
    aggregator = DataAggregator(
        commits=commits,
        prs=prs,
        reviews=reviews,
        work_items=work_items,
        team_members=config.team_members,
    )
    summary_df = aggregator.get_summary()
    timeline_df = aggregator.get_timeline()
    print("✓")
    print()

    # --- Сводная таблица в консоль ---
    if not summary_df.empty:
        print("📋 Сводка по участникам:")
        display_cols = {
            "person": "Участник",
            "commits": "Коммиты",
            "prs_created": "PR",
            "prs_merged": "PR влито",
            "reviews_given": "Ревью",
            "pr_comments": "Коммент.",
            "wi_created": "WI созд.",
            "wi_closed": "WI закр.",
        }
        display_df = summary_df[[c for c in display_cols if c in summary_df.columns]]
        display_df = display_df.rename(columns=display_cols)
        print(tabulate(display_df, headers="keys", tablefmt="rounded_outline", showindex=False))
        print()
    else:
        print("⚠️  Данные не найдены за выбранный период.")
        print()

    # --- Генерация отчётов ---
    output_dir = args.output
    os.makedirs(output_dir, exist_ok=True)
    date_suffix = f"{config.date_from.strftime('%Y%m%d')}_{config.date_to.strftime('%Y%m%d')}"

    if args.format in ("html", "both"):
        html_path = os.path.join(output_dir, f"report_{date_suffix}.html")
        print(f"📄 Генерируем HTML-отчёт...", end=" ", flush=True)
        try:
            from report.html_report import HTMLReport
            HTMLReport(summary_df, timeline_df, config).generate(html_path)
            print(f"✓")
            print(f"   Сохранён: {os.path.abspath(html_path)}")
        except Exception as exc:
            print(f"⚠️  Ошибка: {exc}")

    if args.format in ("excel", "both"):
        xlsx_path = os.path.join(output_dir, f"report_{date_suffix}.xlsx")
        print(f"📊 Генерируем Excel-отчёт...", end=" ", flush=True)
        try:
            from report.excel_report import ExcelReport
            ExcelReport(summary_df, timeline_df, config).generate(xlsx_path)
            print(f"✓")
            print(f"   Сохранён: {os.path.abspath(xlsx_path)}")
        except Exception as exc:
            print(f"⚠️  Ошибка: {exc}")

    print()
    print("✅ Готово!")


if __name__ == "__main__":
    main()
