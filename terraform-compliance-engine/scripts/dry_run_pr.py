"""
Dry-run local que simula la ejecución del engine dentro de GitHub Actions
sobre un PR ficticio.

- Llama al LLM de verdad (necesita OPENAI_API_KEY en el entorno).
- Crea un event.json falso con un PR id arbitrario y exporta GITHUB_*.
- Monkey-patcha requests.get/post/patch para no llamar a la API real de
  GitHub e imprimir el payload exacto que se enviaría.

Uso:
    set -a && . ./.env && set +a
    python scripts/dry_run_pr.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

FAKE_REPO = "fake-org/fake-repo"
FAKE_PR_NUMBER = 42
FAKE_TOKEN = "fake-token-not-real"

console = Console()


class FakeResponse:
    def __init__(self, status_code: int, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


# Buffer para capturar la traza de la API simulada en orden, y pintarla luego
api_trace: list[tuple[str, str, str]] = []  # (verb, url, summary)
captured_body: dict[str, str] = {}


def make_event_file(pr_number: int) -> str:
    fd, path = tempfile.mkstemp(suffix=".json", prefix="gh-event-")
    os.close(fd)
    Path(path).write_text(
        json.dumps({"pull_request": {"number": pr_number}}),
        encoding="utf-8",
    )
    return path


def fake_get(url, headers=None, timeout=None):
    api_trace.append(("GET", url, "200 OK · 0 comentarios previos"))
    return FakeResponse(200, [])


def fake_post(url, headers=None, json=None, timeout=None):
    captured_body["body"] = json["body"]
    api_trace.append(("POST", url, "201 Created · id=999"))
    return FakeResponse(201, {"id": 999})


def fake_patch(url, headers=None, json=None, timeout=None):
    captured_body["body"] = json["body"]
    api_trace.append(("PATCH", url, "200 OK · id=999"))
    return FakeResponse(200, {"id": 999})


def section(title: str) -> None:
    console.print()
    console.print(Rule(f"[bold cyan]{title}[/bold cyan]", style="cyan"))
    console.print()


def main() -> int:
    event_path = make_event_file(FAKE_PR_NUMBER)
    os.environ["GITHUB_REPOSITORY"] = FAKE_REPO
    os.environ["GITHUB_TOKEN"] = FAKE_TOKEN
    os.environ["GITHUB_EVENT_PATH"] = event_path

    # ── Header ──────────────────────────────────────────────────────────
    console.print()
    console.print(
        Panel.fit(
            "[bold]Compliance Engine — Dry Run[/bold]\n"
            "Simulación local de la ejecución en GitHub Actions sobre un PR ficticio.",
            border_style="magenta",
        )
    )

    # ── 1. Setup ────────────────────────────────────────────────────────
    section("1. Setup")
    azure_endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT", "")
    setup = Table.grid(padding=(0, 2))
    setup.add_column(style="bold")
    setup.add_column()
    setup.add_row("Repositorio", FAKE_REPO)
    setup.add_row("PR número", f"#{FAKE_PR_NUMBER}")
    setup.add_row(
        "Cliente LLM",
        "[green]Azure OpenAI[/green]" if azure_endpoint else "[yellow]OpenAI público[/yellow]",
    )
    if azure_endpoint:
        setup.add_row("Endpoint", azure_endpoint)
        setup.add_row("API version", os.environ.get("AZURE_OPENAI_API_VERSION", "(default)"))
    setup.add_row("Modelo", os.environ.get("OPENAI_MODEL", "(default)"))
    setup.add_row("Event file", event_path)
    console.print(setup)

    # ── 2. Llamada al LLM ───────────────────────────────────────────────
    section("2. Llamada al LLM")
    from engine import run as engine_run

    prompt = os.environ.get("PROMPT") or engine_run.DEFAULT_PROMPT
    model = os.environ.get("OPENAI_MODEL") or engine_run.DEFAULT_MODEL
    console.print(f"[dim]Prompt:[/dim] {prompt}")
    console.print()

    with console.status(f"[bold cyan]Llamando a {model}...", spinner="dots"):
        t0 = time.perf_counter()
        raw = engine_run.call_llm(prompt, model)
        elapsed = time.perf_counter() - t0

    console.print(
        Panel(raw or "[i]vacío[/i]", title="[green]Respuesta del LLM[/green]", border_style="green")
    )
    console.print(f"[dim]Tiempo: {elapsed:.2f}s[/dim]")

    # ── 3. Parseo ───────────────────────────────────────────────────────
    section("3. Parseo de la respuesta")
    parsed = engine_run.parse_response(raw)
    parsed_table = Table(show_header=True, header_style="bold")
    parsed_table.add_column("Campo")
    parsed_table.add_column("Valor")
    for k, v in parsed.items():
        parsed_table.add_row(k, str(v))
    console.print(parsed_table)

    # ── 4. GitHub API simulada ──────────────────────────────────────────
    section("4. GitHub API (simulada)")
    body = engine_run.render_comment(parsed, model)
    with (
        patch.object(engine_run.requests, "get", side_effect=fake_get),
        patch.object(engine_run.requests, "post", side_effect=fake_post),
        patch.object(engine_run.requests, "patch", side_effect=fake_patch),
    ):
        # Llamamos directamente a upsert para no duplicar el LLM call
        engine_run.upsert_pr_comment(body)

    api_table = Table(show_header=True, header_style="bold")
    api_table.add_column("Verbo", style="bold")
    api_table.add_column("URL")
    api_table.add_column("Respuesta simulada")
    for verb, url, summary in api_trace:
        color = {"GET": "blue", "POST": "green", "PATCH": "yellow"}.get(verb, "white")
        api_table.add_row(f"[{color}]{verb}[/{color}]", url, summary)
    console.print(api_table)

    # ── 5. Comentario renderizado ───────────────────────────────────────
    section("5. Comentario tal y como aparecería en el PR")
    rendered = captured_body.get("body", body)
    # quitamos el marcador HTML para que el render se vea limpio
    visible = rendered.replace("<!-- compliance-report -->\n", "")
    console.print(
        Panel(
            Markdown(visible),
            title=f"[bold]💬 Comentario en PR #{FAKE_PR_NUMBER}[/bold]",
            border_style="cyan",
            padding=(1, 2),
        )
    )

    # ── 6. Resultado ────────────────────────────────────────────────────
    section("✅ Resultado")
    summary = Table.grid(padding=(0, 2))
    summary.add_column(style="bold")
    summary.add_column()
    summary.add_row("LLM", "[green]OK[/green]")
    summary.add_row("Parseo", "[green]OK[/green]")
    summary.add_row("GitHub API", f"[green]OK[/green] · {len(api_trace)} llamadas")
    summary.add_row("Comentario", f"[green]Publicado en PR #{FAKE_PR_NUMBER}[/green]")
    console.print(summary)
    console.print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
