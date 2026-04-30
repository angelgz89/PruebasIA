"""
Analyzer — evalúa cada control aplicable contra el código Terraform usando LLM.

Una llamada por (control, recurso). El LLM recibe el control y los atributos
del recurso, y devuelve un veredicto JSON estructurado: status / evidence /
gaps / remediation / confidence.
"""

from __future__ import annotations

import json
import os
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Iterable

from openai import AzureOpenAI, OpenAI

DEFAULT_MAX_WORKERS = 8

SYSTEM_PROMPT = """Eres un experto en ciberseguridad cloud especializado en Azure e infraestructura como código.
Tu tarea es evaluar si el recurso Terraform proporcionado cumple con el control de seguridad indicado.

REGLAS:
1. Evalúa SOLO lo que está en los atributos del recurso. No asumas configuraciones que no ves.
2. Si el control no se puede determinar con la información disponible, devuelve status MANUAL_REVIEW.
3. Sé específico en las evidencias: cita el atributo concreto y su valor.
4. Responde ÚNICAMENTE con un JSON válido (sin code fences, sin texto adicional) con este schema exacto:
   {
     "status": "PASS|FAIL|PARTIAL|MANUAL_REVIEW",
     "evidence": "string corta — atributo y valor que justifica el veredicto",
     "gaps": ["string", ...],
     "remediation": "string corta — cambio Terraform sugerido (vacío si PASS)",
     "confidence": 0.0
   }"""

VALID_STATUSES = {"PASS", "FAIL", "PARTIAL", "MANUAL_REVIEW"}


@dataclass
class ControlVerdict:
    control_id: str
    standard: str
    severity: str
    title: str
    resource_type: str
    resource_name: str
    status: str
    evidence: str
    gaps: list[str]
    remediation: str
    confidence: float
    raw_response: str = ""


@dataclass
class AnalyzerResult:
    model: str
    verdicts: list[ControlVerdict] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.verdicts)

    @property
    def by_status(self) -> dict[str, int]:
        counts: dict[str, int] = {s: 0 for s in VALID_STATUSES}
        for v in self.verdicts:
            counts[v.status] = counts.get(v.status, 0) + 1
        return counts


def build_client() -> tuple[Any, bool]:
    """Construye AzureOpenAI si AZURE_OPENAI_ENDPOINT está set, si no OpenAI público."""
    azure_endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT")
    if azure_endpoint:
        return (
            AzureOpenAI(
                azure_endpoint=azure_endpoint,
                api_key=os.environ["OPENAI_API_KEY"],
                api_version=os.environ.get("AZURE_OPENAI_API_VERSION", "2025-04-01-preview"),
            ),
            True,
        )
    return OpenAI(), False


def call_llm(client, is_azure: bool, model: str, system: str, user: str) -> str:
    """Una llamada al LLM con system + user prompt. Devuelve el texto crudo."""
    if is_azure:
        combined = f"{system}\n\n---\n\n{user}"
        response = client.responses.create(model=model, input=combined)
        for block in response.output:
            if block.type == "message":
                return block.content[0].text.strip()
        return ""
    response = client.chat.completions.create(
        model=model,
        max_tokens=1024,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    return (response.choices[0].message.content or "").strip()


def _build_user_prompt(assignment) -> str:
    attrs_json = json.dumps(
        assignment.resource_attributes, indent=2, ensure_ascii=False, default=str
    )
    focus = assignment.evaluation_focus.strip() if assignment.evaluation_focus else "(no especificado)"
    return f"""CONTROL:
- ID: {assignment.control_id}
- Estándar: {assignment.standard}
- Título: {assignment.title}
- Severidad: {assignment.severity}
- Qué verificar:
{focus}

RECURSO TERRAFORM:
- Tipo: {assignment.resource_type}
- Nombre: {assignment.resource_name}
- Fichero: {assignment.source_file}
- Atributos:
{attrs_json}"""


def _parse_verdict_json(raw: str) -> dict[str, Any]:
    text = raw.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            return json.loads(match.group(0))
        raise


def evaluate_one(client, is_azure: bool, model: str, assignment) -> ControlVerdict:
    user_prompt = _build_user_prompt(assignment)
    raw = call_llm(client, is_azure, model, SYSTEM_PROMPT, user_prompt)

    try:
        data = _parse_verdict_json(raw)
        status = str(data.get("status") or "MANUAL_REVIEW").upper()
        if status not in VALID_STATUSES:
            status = "MANUAL_REVIEW"
        evidence = str(data.get("evidence") or "")
        gaps_raw = data.get("gaps") or []
        gaps = [str(g) for g in gaps_raw] if isinstance(gaps_raw, list) else []
        remediation = str(data.get("remediation") or "")
        try:
            confidence = float(data.get("confidence") or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
    except (json.JSONDecodeError, ValueError) as exc:
        status = "MANUAL_REVIEW"
        evidence = f"Respuesta del LLM no parseable como JSON ({type(exc).__name__})."
        gaps = []
        remediation = ""
        confidence = 0.0

    return ControlVerdict(
        control_id=assignment.control_id,
        standard=assignment.standard,
        severity=assignment.severity,
        title=assignment.title,
        resource_type=assignment.resource_type,
        resource_name=assignment.resource_name,
        status=status,
        evidence=evidence,
        gaps=gaps,
        remediation=remediation,
        confidence=confidence,
        raw_response=raw,
    )


def _max_workers(assignments_count: int) -> int:
    """Resuelve cuántos workers usar a partir de ANALYZER_MAX_WORKERS y el
    nº de assignments. Mínimo 1, máximo el nº de assignments (no tiene
    sentido tener más workers que tareas)."""
    raw = os.environ.get("ANALYZER_MAX_WORKERS")
    try:
        configured = int(raw) if raw else DEFAULT_MAX_WORKERS
    except ValueError:
        configured = DEFAULT_MAX_WORKERS
    return max(1, min(configured, max(1, assignments_count)))


def evaluate_assignments(model: str, assignments: Iterable) -> AnalyzerResult:
    """Construye un cliente y evalúa los assignments en paralelo (threads).

    Las llamadas al LLM son I/O-bound y el SDK de OpenAI es thread-safe, así
    que `ThreadPoolExecutor` con un único cliente compartido es lo más
    eficiente. `ex.map` preserva el orden de entrada en la salida.
    """
    client, is_azure = build_client()
    assignments_list = list(assignments)
    if not assignments_list:
        return AnalyzerResult(model=model, verdicts=[])

    workers = _max_workers(len(assignments_list))
    if workers == 1:
        verdicts = [evaluate_one(client, is_azure, model, a) for a in assignments_list]
    else:
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="analyzer") as ex:
            verdicts = list(
                ex.map(lambda a: evaluate_one(client, is_azure, model, a), assignments_list)
            )
    return AnalyzerResult(model=model, verdicts=verdicts)
