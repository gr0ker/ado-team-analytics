"""
Генератор HTML-отчёта с интерактивными графиками Plotly.
"""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd
import plotly.graph_objects as go
import plotly.io as pio
from jinja2 import Environment, FileSystemLoader

from config import AppConfig

_TEMPLATE_DIR = Path(__file__).parent / "templates"

# Цветовая палитра для участников
_PALETTE = [
    "#0078d4", "#e74c3c", "#2ecc71", "#f39c12", "#9b59b6",
    "#1abc9c", "#e67e22", "#3498db", "#e91e63", "#00bcd4",
]


def _fig_to_html(fig: go.Figure, first: bool = False) -> str:
    """Конвертирует фигуру Plotly в HTML-фрагмент."""
    return pio.to_html(
        fig,
        full_html=False,
        include_plotlyjs="cdn" if first else False,
        config={"responsive": True, "displayModeBar": False},
    )


class HTMLReport:
    """Генерирует самодостаточный HTML-отчёт."""

    def __init__(
        self,
        summary_df: pd.DataFrame,
        timeline_df: pd.DataFrame,
        config: AppConfig,
    ) -> None:
        self.summary_df = summary_df
        self.timeline_df = timeline_df
        self.config = config

    def generate(self, output_path: str) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)

        persons = list(self.summary_df["person"]) if not self.summary_df.empty else []
        color_map = {p: _PALETTE[i % len(_PALETTE)] for i, p in enumerate(persons)}

        chart_commits = _fig_to_html(self._chart_commits(color_map), first=True)
        chart_prs = _fig_to_html(self._chart_prs(color_map))
        chart_reviews = _fig_to_html(self._chart_reviews(color_map))
        chart_work_items = _fig_to_html(self._chart_work_items(color_map))
        chart_heatmap = _fig_to_html(self._chart_heatmap())

        summary_table = self._summary_html()

        env = Environment(loader=FileSystemLoader(str(_TEMPLATE_DIR)), autoescape=False)
        template = env.get_template("report.html")
        html = template.render(
            date_from=self.config.date_from.strftime("%d.%m.%Y"),
            date_to=self.config.date_to.strftime("%d.%m.%Y"),
            projects=", ".join(self.config.projects),
            generated_at=datetime.now().strftime("%d.%m.%Y %H:%M"),
            summary_table=summary_table,
            chart_commits=chart_commits,
            chart_prs=chart_prs,
            chart_reviews=chart_reviews,
            chart_work_items=chart_work_items,
            chart_heatmap=chart_heatmap,
        )

        with open(output_path, "w", encoding="utf-8") as f:
            f.write(html)

    # ------------------------------------------------------------------
    # Таблица сводки
    # ------------------------------------------------------------------

    def _summary_html(self) -> str:
        if self.summary_df.empty:
            return "<p>Нет данных за выбранный период.</p>"

        col_labels = {
            "person": "Участник",
            "commits": "Коммиты",
            "lines_added": "+ строк",
            "lines_deleted": "− строк",
            "prs_created": "PR создано",
            "prs_merged": "PR влито",
            "prs_abandoned": "PR закрыто",
            "reviews_given": "Ревью",
            "reviews_approved": "✅",
            "reviews_suggestions": "💬",
            "reviews_rejected": "❌",
            "pr_comments": "Комментарии",
            "wi_created": "WI создано",
            "wi_closed": "WI закрыто",
            "wi_assigned": "WI назначено",
        }

        df = self.summary_df.rename(columns=col_labels)
        cols = [col_labels.get(c, c) for c in self.summary_df.columns]
        df = df[[c for c in cols if c in df.columns]]

        rows_html = ""
        for _, row in df.iterrows():
            cells = "".join(f"<td>{v}</td>" for v in row)
            rows_html += f"<tr>{cells}</tr>"

        headers = "".join(f"<th>{c}</th>" for c in df.columns)
        return (
            f"<table><thead><tr>{headers}</tr></thead>"
            f"<tbody>{rows_html}</tbody></table>"
        )

    # ------------------------------------------------------------------
    # Графики
    # ------------------------------------------------------------------

    def _chart_commits(self, color_map: dict) -> go.Figure:
        fig = go.Figure()
        if self.timeline_df.empty:
            return self._empty_fig("Нет данных")

        weeks = sorted(self.timeline_df["week"].unique())
        for person in sorted(self.timeline_df["person"].unique()):
            sub = self.timeline_df[self.timeline_df["person"] == person]
            values = [
                int(sub[sub["week"] == w]["commits"].sum()) for w in weeks
            ]
            fig.add_trace(go.Bar(
                name=person, x=weeks, y=values,
                marker_color=color_map.get(person, "#999"),
            ))

        fig.update_layout(
            barmode="stack", margin=dict(t=20, b=40, l=40, r=20),
            height=320, xaxis_title="Неделя", yaxis_title="Коммиты",
            legend=dict(orientation="h", y=-0.25),
            plot_bgcolor="white", paper_bgcolor="white",
        )
        fig.update_xaxes(tickangle=-45)
        return fig

    def _chart_prs(self, color_map: dict) -> go.Figure:
        fig = go.Figure()
        if self.timeline_df.empty:
            return self._empty_fig("Нет данных")

        weeks = sorted(self.timeline_df["week"].unique())
        all_created = [int(self.timeline_df[self.timeline_df["week"] == w]["prs_created"].sum()) for w in weeks]
        all_merged = [int(self.timeline_df[self.timeline_df["week"] == w]["prs_merged"].sum()) for w in weeks]

        fig.add_trace(go.Bar(name="Создано", x=weeks, y=all_created, marker_color="#0078d4"))
        fig.add_trace(go.Bar(name="Влито", x=weeks, y=all_merged, marker_color="#2ecc71"))
        fig.update_layout(
            barmode="group", margin=dict(t=20, b=40, l=40, r=20),
            height=320, xaxis_title="Неделя", yaxis_title="Pull Requests",
            legend=dict(orientation="h", y=-0.25),
            plot_bgcolor="white", paper_bgcolor="white",
        )
        fig.update_xaxes(tickangle=-45)
        return fig

    def _chart_reviews(self, color_map: dict) -> go.Figure:
        fig = go.Figure()
        if self.timeline_df.empty:
            return self._empty_fig("Нет данных")

        weeks = sorted(self.timeline_df["week"].unique())
        for person in sorted(self.timeline_df["person"].unique()):
            sub = self.timeline_df[self.timeline_df["person"] == person]
            values = [int(sub[sub["week"] == w]["reviews_given"].sum()) for w in weeks]
            fig.add_trace(go.Bar(
                name=person, x=weeks, y=values,
                marker_color=color_map.get(person, "#999"),
            ))

        fig.update_layout(
            barmode="stack", margin=dict(t=20, b=40, l=40, r=20),
            height=320, xaxis_title="Неделя", yaxis_title="Ревью",
            legend=dict(orientation="h", y=-0.25),
            plot_bgcolor="white", paper_bgcolor="white",
        )
        fig.update_xaxes(tickangle=-45)
        return fig

    def _chart_work_items(self, color_map: dict) -> go.Figure:
        fig = go.Figure()
        if self.timeline_df.empty:
            return self._empty_fig("Нет данных")

        weeks = sorted(self.timeline_df["week"].unique())
        wi_created = [int(self.timeline_df[self.timeline_df["week"] == w]["wi_created"].sum()) for w in weeks]
        wi_closed = [int(self.timeline_df[self.timeline_df["week"] == w]["wi_closed"].sum()) for w in weeks]

        fig.add_trace(go.Bar(name="Создано", x=weeks, y=wi_created, marker_color="#f39c12"))
        fig.add_trace(go.Bar(name="Закрыто", x=weeks, y=wi_closed, marker_color="#9b59b6"))
        fig.update_layout(
            barmode="group", margin=dict(t=20, b=40, l=40, r=20),
            height=320, xaxis_title="Неделя", yaxis_title="Рабочие элементы",
            legend=dict(orientation="h", y=-0.25),
            plot_bgcolor="white", paper_bgcolor="white",
        )
        fig.update_xaxes(tickangle=-45)
        return fig

    def _chart_heatmap(self) -> go.Figure:
        if self.timeline_df.empty:
            return self._empty_fig("Нет данных")

        df = self.timeline_df.copy()
        df["total"] = (
            df["commits"] + df["prs_created"] + df["reviews_given"]
            + df["pr_comments"] + df["wi_created"] + df["wi_closed"]
        )

        persons = sorted(df["person"].unique())
        weeks = sorted(df["week"].unique())

        z = []
        for person in persons:
            row = []
            for week in weeks:
                val = df[(df["person"] == person) & (df["week"] == week)]["total"].sum()
                row.append(int(val))
            z.append(row)

        fig = go.Figure(go.Heatmap(
            z=z,
            x=weeks,
            y=persons,
            colorscale="Blues",
            text=z,
            texttemplate="%{text}",
            textfont={"size": 10},
            showscale=True,
            colorbar=dict(title="Активность"),
        ))
        fig.update_layout(
            margin=dict(t=20, b=80, l=160, r=20),
            height=max(280, 60 * len(persons)),
            xaxis_title="Неделя",
            yaxis_title="Участник",
            plot_bgcolor="white",
            paper_bgcolor="white",
        )
        fig.update_xaxes(tickangle=-45)
        return fig

    @staticmethod
    def _empty_fig(msg: str) -> go.Figure:
        fig = go.Figure()
        fig.add_annotation(text=msg, xref="paper", yref="paper", x=0.5, y=0.5,
                           showarrow=False, font=dict(size=14, color="#999"))
        fig.update_layout(height=200, plot_bgcolor="white", paper_bgcolor="white",
                          margin=dict(t=10, b=10, l=10, r=10))
        return fig
