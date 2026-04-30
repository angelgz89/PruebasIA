"""
Terraform Compliance Engine — MVP step 4.

Orquesta el pipeline:
1. collector — lee .tf con python-hcl2 y extrae recursos azurerm_*
2. mapper — lookup determinista contra master-mapping para resolver
   qué controles aplican a cada recurso
3. analyzer — una llamada LLM por (control, recurso) que devuelve un
   veredicto JSON estructurado: PASS / FAIL / PARTIAL / MANUAL_REVIEW
4. publica el reporte como comentario en el PR de GitHub Actions
   (upsert por marcador, no duplica)
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from analyzer import AnalyzerResult, evaluate_assignments  # noqa: E402
from collector import CollectorResult, collect_terraform  # noqa: E402
from mapper import MappingResult, resolve_controls  # noqa: E402

COMMENT_MARKER = "<!-- compliance-report -->"
DEFAULT_MODEL = "gpt-5.4-pro"
DEFAULT_REPO_PATH = "scripts/example-terraform"
DEFAULT_MAPPING_PATH = "mappings/master-mapping.yaml"

SEVERITY_ICONS = {
    "critical": "🔴",
    "high": "🟠",
    "medium": "🟡",
    "low": "🔵",
}

STATUS_ICONS = {
    "PASS": "✅",
    "FAIL": "❌",
    "PARTIAL": "⚠️",
    "MANUAL_REVIEW": "🔍",
}


def _truncate_for_table(text: str, limit: int = 140) -> str:
    if not text:
        return "—"
    cleaned = text.replace("|", "\\|").replace("\n", " ").strip()
    return cleaned if len(cleaned) <= limit else cleaned[: limit - 1] + "…"


def render_comment(
    model: str,
    collected: CollectorResult | None,
    mapping: MappingResult | None,
    analysis: AnalyzerResult | None,
) -> str:
    lines = [
        COMMENT_MARKER,
        "## Compliance Engine — MVP step 4",
        "",
    ]

    if analysis is not None:
        by_status = analysis.by_status
        lines += [
            "### Resumen",
            "",
            "| Métrica | Valor |",
            "| --- | --- |",
            f"| Modelo | `{model}` |",
            f"| Recursos analizados | {len(collected.resources) if collected else 0} |",
            f"| Controles evaluados | {analysis.total} |",
            f"| ✅ PASS | {by_status.get('PASS', 0)} |",
            f"| ❌ FAIL | {by_status.get('FAIL', 0)} |",
            f"| ⚠️ PARTIAL | {by_status.get('PARTIAL', 0)} |",
            f"| 🔍 MANUAL_REVIEW | {by_status.get('MANUAL_REVIEW', 0)} |",
            "",
        ]

    if collected is not None:
        by_type = collected.by_type()
        lines += [
            "### Terraform escaneado",
            "",
            f"- Path analizado: `{collected.repo_path}`",
            f"- Ficheros `.tf` parseados: {collected.files_parsed} / {collected.files_scanned}",
            f"- Recursos totales: {len(collected.resources)}",
            f"- Tipos `azurerm_*` únicos: {len(collected.azurerm_resource_types)}",
        ]
        if collected.parse_errors:
            lines.append(f"- ⚠️ Errores de parseo: {len(collected.parse_errors)}")
        if collected.azurerm_resource_types:
            lines += [
                "",
                "**Recursos detectados:**",
                "",
                "| Tipo | Cantidad | Instancias |",
                "| --- | --- | --- |",
            ]
            for rtype in collected.azurerm_resource_types:
                instances = ", ".join(f"`{r.name}`" for r in by_type[rtype])
                lines.append(f"| `{rtype}` | {len(by_type[rtype])} | {instances} |")
        lines.append("")

    if mapping is not None and mapping.resources_without_controls:
        no_ctrl = ", ".join(f"`{t}`" for t in mapping.resources_without_controls)
        lines += [
            "**Tipos sin controles en el mapping**: " + no_ctrl,
            "",
        ]

    if analysis is not None and analysis.verdicts:
        lines += [
            "### Resultados de la evaluación",
            "",
            "| Recurso | Control | Severidad | Estado | Evidencia | Confianza |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
        for v in analysis.verdicts:
            sev_icon = SEVERITY_ICONS.get(v.severity, "")
            sev = f"{sev_icon} {v.severity}".strip()
            status_icon = STATUS_ICONS.get(v.status, "")
            status = f"{status_icon} {v.status}".strip()
            evidence = _truncate_for_table(v.evidence)
            resource = f"`{v.resource_type}.{v.resource_name}`"
            lines.append(
                f"| {resource} | `{v.control_id}` | {sev} | {status} | {evidence} | {v.confidence:.2f} |"
            )
        lines.append("")

        fails = [v for v in analysis.verdicts if v.status in ("FAIL", "PARTIAL")]
        if fails:
            lines += [
                "<details>",
                "<summary>📋 Detalle de hallazgos (FAIL / PARTIAL)</summary>",
                "",
            ]
            for v in fails:
                lines += [
                    f"#### {STATUS_ICONS.get(v.status, '')} `{v.control_id}` — {v.title}",
                    "",
                    f"- **Recurso**: `{v.resource_type}.{v.resource_name}`",
                    f"- **Severidad**: {SEVERITY_ICONS.get(v.severity, '')} {v.severity}",
                    f"- **Evidencia**: {v.evidence or '—'}",
                ]
                if v.gaps:
                    lines.append("- **Gaps**:")
                    for gap in v.gaps:
                        lines.append(f"  - {gap}")
                if v.remediation:
                    lines.append(f"- **Remediación sugerida**: {v.remediation}")
                lines.append("")
            lines.append("</details>")

    return "\n".join(lines) + "\n"


def get_pr_number() -> int | None:
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    if not event_path or not Path(event_path).exists():
        return None
    with open(event_path, encoding="utf-8") as f:
        event = json.load(f)
    pr = event.get("pull_request") or {}
    return pr.get("number")


def find_existing_comment(repo: str, pr_number: int, token: str) -> int | None:
    url = f"https://api.github.com/repos/{repo}/issues/{pr_number}/comments"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    resp = requests.get(url, headers=headers, timeout=30)
    resp.raise_for_status()
    for comment in resp.json():
        if COMMENT_MARKER in (comment.get("body") or ""):
            return comment["id"]
    return None


def upsert_pr_comment(body: str) -> None:
    repo = os.environ.get("GITHUB_REPOSITORY")
    token = os.environ.get("GITHUB_TOKEN")
    pr_number = get_pr_number()

    if not (repo and token and pr_number):
        print("[info] No estamos en un PR de GitHub Actions; salto la publicación del comentario.")
        return

    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    existing_id = find_existing_comment(repo, pr_number, token)

    if existing_id:
        url = f"https://api.github.com/repos/{repo}/issues/comments/{existing_id}"
        resp = requests.patch(url, headers=headers, json={"body": body}, timeout=30)
    else:
        url = f"https://api.github.com/repos/{repo}/issues/{pr_number}/comments"
        resp = requests.post(url, headers=headers, json={"body": body}, timeout=30)

    resp.raise_for_status()
    print(f"[ok] Comentario {'actualizado' if existing_id else 'creado'} en PR #{pr_number}.")


def main() -> int:
    if not os.environ.get("OPENAI_API_KEY"):
        print("[error] Falta OPENAI_API_KEY en el entorno.", file=sys.stderr)
        return 1

    model = os.environ.get("OPENAI_MODEL") or DEFAULT_MODEL
    repo_path = os.environ.get("REPO_PATH") or DEFAULT_REPO_PATH
    mapping_path = os.environ.get("MAPPING_PATH") or DEFAULT_MAPPING_PATH

    collected: CollectorResult | None = None
    if Path(repo_path).exists():
        collected = collect_terraform(repo_path)
        print(
            f"[info] Terraform: {collected.files_parsed}/{collected.files_scanned} .tf parseados, "
            f"{len(collected.resources)} recursos, "
            f"{len(collected.azurerm_resource_types)} tipos azurerm"
        )
        if collected.parse_errors:
            print(f"[warn] {len(collected.parse_errors)} errores de parseo:")
            for err in collected.parse_errors:
                print(f"  - {err['file']}: {err['error']}")
    else:
        print(f"[info] REPO_PATH '{repo_path}' no existe; salto el escaneo de Terraform.")

    mapping: MappingResult | None = None
    if collected is not None and Path(mapping_path).exists():
        mapping = resolve_controls(collected, mapping_path)
        print(
            f"[info] Mapping: {mapping.total_assignments} asignaciones recurso↔control "
            f"({mapping.assignments_by_standard})"
        )
        if mapping.resources_without_controls:
            print(
                f"[info] Tipos sin controles en el mapping: "
                f"{', '.join(mapping.resources_without_controls)}"
            )
    elif collected is not None:
        print(f"[info] MAPPING_PATH '{mapping_path}' no existe; salto el lookup de controles.")

    analysis: AnalyzerResult | None = None
    if mapping is not None and mapping.assignments:
        print(f"[info] Evaluando {len(mapping.assignments)} controles con {model}…")
        analysis = evaluate_assignments(model, mapping.assignments)
        print(f"[info] Veredictos: {analysis.by_status}")

    body = render_comment(model, collected, mapping, analysis)
    print("---- Comentario ----")
    print(body)

    upsert_pr_comment(body)
    return 0


if __name__ == "__main__":
    sys.exit(main())
