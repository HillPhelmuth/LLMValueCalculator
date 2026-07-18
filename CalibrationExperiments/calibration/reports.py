from __future__ import annotations

import html
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class CalibrationCard:
    title: str
    experiment_id: str
    coverage: dict[str, Any]
    exclusions: dict[str, int]
    costs: dict[str, Any]
    fit_diagnostics: dict[str, Any]
    holdout_metrics: dict[str, Any]
    intervals: dict[str, Any]
    sensitivity: dict[str, Any]
    decisions: tuple[dict[str, Any], ...] = ()
    provenance_ids: tuple[str, ...] = ()
    estimate_ids: tuple[str, ...] = ()

    def to_json(self) -> dict[str, Any]:
        return asdict(self) | {
            "decisions": list(self.decisions),
            "provenance_ids": list(self.provenance_ids),
            "estimate_ids": list(self.estimate_ids),
        }


def render_markdown(card: CalibrationCard) -> str:
    lines = [
        f"# {card.title}",
        "",
        f"Experiment: `{card.experiment_id}`",
        "",
        "## Coverage",
        "",
        _bullet_table(card.coverage),
        "",
        "## Exclusions",
        "",
        _bullet_table(card.exclusions),
        "",
        "## Costs",
        "",
        _bullet_table(card.costs),
        "",
        "## Fit diagnostics",
        "",
        _bullet_table(card.fit_diagnostics),
        "",
        "## Holdout metrics and intervals",
        "",
        _bullet_table(card.holdout_metrics | {"intervals": card.intervals}),
        "",
        "## Sensitivity and decisions",
        "",
        _bullet_table(card.sensitivity),
    ]
    for decision in card.decisions:
        lines.extend(("", f"- Decision: `{decision.get('parameter', 'unknown')}` → **{decision.get('decision', 'unknown')}** ({decision.get('rationale', '')})"))
    if card.estimate_ids or card.provenance_ids:
        lines.extend(("", "## Lineage", "", f"- Estimate IDs: {', '.join(f'`{item}`' for item in card.estimate_ids) or 'none'}", f"- Provenance IDs: {', '.join(f'`{item}`' for item in card.provenance_ids) or 'none'}"))
    return "\n".join(lines) + "\n"


def render_html(card: CalibrationCard) -> str:
    markdown = render_markdown(card)
    body = "<pre>" + html.escape(markdown) + "</pre>"
    return f"<!doctype html><html><head><meta charset='utf-8'><title>{html.escape(card.title)}</title></head><body>{body}{render_split_svg(card.holdout_metrics)}</body></html>\n"


def render_split_svg(metrics: dict[str, Any]) -> str:
    """Render a dependency-free split diagnostic plot for train/validation/holdout."""
    values = metrics.get("split_values", {})
    if not isinstance(values, dict):
        values = {}
    bars = []
    for index, split in enumerate(("fit", "validation", "dataset_holdout", "model_holdout")):
        value = float(values.get(split, 0.0))
        height = max(0.0, min(100.0, value * 100))
        x = 20 + index * 90
        bars.append(f"<rect x='{x}' y='{120-height}' width='50' height='{height}'><title>{html.escape(split)}: {value:.4f}</title></rect><text x='{x}' y='140'>{html.escape(split)}</text>")
    return "<svg viewBox='0 0 420 160' role='img' aria-label='split diagnostics'>" + "".join(bars) + "</svg>"


def write_calibration_card(card: CalibrationCard, directory: str | Path) -> tuple[Path, Path, Path]:
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    markdown_path = root / f"{card.experiment_id}.md"
    html_path = root / f"{card.experiment_id}.html"
    json_path = root / f"{card.experiment_id}.json"
    markdown_path.write_text(render_markdown(card), encoding="utf-8")
    html_path.write_text(render_html(card), encoding="utf-8")
    json_path.write_text(json.dumps(card.to_json(), sort_keys=True, indent=2), encoding="utf-8")
    return markdown_path, html_path, json_path


def _bullet_table(values: dict[str, Any]) -> str:
    return "\n".join(f"- `{key}`: `{value}`" for key, value in sorted(values.items())) or "- none"
