"""
Terraform Compliance Engine — MVP step 3.

Step 1: Llamada hello-world a un LLM y publicación del resultado en el PR.
Step 2: Lectura de archivos .tf con python-hcl2 y extracción de recursos
        azurerm_*.
Step 3: Lookup determinista contra `mappings/master-mapping.yaml` para
        decidir qué controles aplican a cada recurso (sin LLM aún).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import requests
from openai import AzureOpenAI, OpenAI

# Asegura que los módulos en engine/ sean importables tanto si se invoca
# `python engine/run.py` como si se invoca como módulo desde otro script.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from collector import CollectorResult, collect_terraform  # noqa: E402
from mapper import MappingResult, resolve_controls  # noqa: E402

COMMENT_MARKER = "<!-- compliance-report -->"
DEFAULT_MODEL = "gpt-5.4-pro"
DEFAULT_PROMPT = "Di 'hola mundo' en una sola frase, en español, sin texto extra."
DEFAULT_REPO_PATH = "scripts/example-terraform"
DEFAULT_MAPPING_PATH = "mappings/master-mapping.yaml"

SEVERITY_ICONS = {
    "critical": "🔴",
    "high": "🟠",
    "medium": "🟡",
    "low": "🔵",
}


def _build_client():
    """Return an AzureOpenAI client if AZURE_OPENAI_ENDPOINT is set, else plain OpenAI."""
    azure_endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT")
    if azure_endpoint:
        return AzureOpenAI(
            azure_endpoint=azure_endpoint,
            api_key=os.environ["OPENAI_API_KEY"],
            api_version=os.environ.get("AZURE_OPENAI_API_VERSION", "2025-04-01-preview"),
        ), True
    return OpenAI(), False


def call_llm(prompt: str, model: str) -> str:
    client, is_azure = _build_client()
    # gpt-5.4-pro on Azure only supports the Responses API
    if is_azure:
        response = client.responses.create(
            model=model,
            input=prompt,
        )
        for block in response.output:
            if block.type == "message":
                return block.content[0].text.strip()
        return ""
    response = client.chat.completions.create(
        model=model,
        max_tokens=256,
        messages=[{"role": "user", "content": prompt}],
    )
    return (response.choices[0].message.content or "").strip()


def parse_response(raw: str) -> dict:
    return {
        "greeting": raw,
        "length": len(raw),
        "ok": bool(raw),
    }


def render_comment(
    parsed: dict,
    model: str,
    collected: CollectorResult | None = None,
    mapping: MappingResult | None = None,
) -> str:
    lines = [
        COMMENT_MARKER,
        "## Compliance Engine — MVP step 3",
        "",
        f"Llamada al LLM (`{model}`) realizada correctamente.",
        "",
        "| Campo | Valor |",
        "| --- | --- |",
        f"| Respuesta | {parsed['greeting']} |",
        f"| Longitud | {parsed['length']} |",
        f"| OK | {parsed['ok']} |",
    ]

    if collected is not None:
        by_type = collected.by_type()
        lines += [
            "",
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

    if mapping is not None:
        lines += [
            "",
            "### Controles aplicables (sin evaluar aún)",
            "",
            f"- Mapping: `{mapping.mapping_path}`",
            f"- Total asignaciones recurso↔control: {mapping.total_assignments}",
        ]
        by_std = mapping.assignments_by_standard
        if by_std:
            counts = " · ".join(f"{std}: {n}" for std, n in by_std.items())
            lines.append(f"- Por estándar: {counts}")
        if mapping.resources_without_controls:
            no_ctrl = ", ".join(f"`{t}`" for t in mapping.resources_without_controls)
            lines.append(f"- Tipos sin controles en el mapping: {no_ctrl}")
        if mapping.assignments:
            lines += [
                "",
                "| Recurso | Control | Severidad | Título |",
                "| --- | --- | --- | --- |",
            ]
            for a in mapping.assignments:
                icon = SEVERITY_ICONS.get(a.severity, "")
                sev = f"{icon} {a.severity}".strip()
                resource = f"`{a.resource_type}.{a.resource_name}`"
                lines.append(f"| {resource} | `{a.control_id}` | {sev} | {a.title} |")

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
    prompt = os.environ.get("PROMPT") or DEFAULT_PROMPT
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

    raw = call_llm(prompt, model)
    parsed = parse_response(raw)
    body = render_comment(parsed, model, collected, mapping)

    print("---- Respuesta LLM ----")
    print(raw)
    print("---- Comentario ----")
    print(body)

    upsert_pr_comment(body)
    return 0


if __name__ == "__main__":
    sys.exit(main())
