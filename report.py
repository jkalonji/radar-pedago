"""Source health report (Markdown), written to the GitHub Actions job summary and to output/."""

import os
from dataclasses import asdict

from models import DOMAINS


def sources_markdown(reports, title: str = "État des sources") -> str:
    ok = [r for r in reports if not r.error]
    lines = [f"## {title}", "",
             f"{len(ok)}/{len(reports)} sources répondent · {sum(r.fetched for r in reports)} éléments récupérés", "",
             "| Domaine | Source | Méthode | Récupérés | Gardés | Détail |", "|---|---|---|---:|---:|---|"]
    order = {d: i for i, d in enumerate(DOMAINS)}
    for r in sorted(reports, key=lambda r: (bool(not r.error), order.get(r.domain, 99), r.name)):
        status = "❌ " + r.error if r.error else (r.note or ("exemple : " + r.sample[0] if r.sample else ""))
        dom = DOMAINS.get(r.domain, {}).get("label", r.domain)
        detail = status.replace("|", "/")[:180]
        lines.append(f"| {dom} | {r.name} | {r.method or r.type} | {r.fetched} | {r.kept} | {detail} |")
    return "\n".join(lines) + "\n"


def write_report(reports, path: str = "output/sources_report.md", title: str = "État des sources") -> str:
    md = sources_markdown(reports, title)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(md)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(md)
    return md


def reports_as_dicts(reports) -> list[dict]:
    return [asdict(r) for r in reports]
