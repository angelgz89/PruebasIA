"""
Collector — lee archivos .tf de un directorio y extrae los recursos declarados.

Es la primera fase determinística del pipeline: NO usa LLM. Devuelve una
estructura plana que las fases siguientes (analyzer) consumirán.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import hcl2

_BLOCK_META_KEYS = {"__is_block__", "__comments__"}


def _strip_quotes(value: Any) -> Any:
    if isinstance(value, str) and len(value) >= 2 and value.startswith('"') and value.endswith('"'):
        return value[1:-1]
    return value


def _clean(value: Any) -> Any:
    """Quita comillas de strings y metadatos de bloques recursivamente."""
    if isinstance(value, dict):
        return {_strip_quotes(k): _clean(v) for k, v in value.items() if k not in _BLOCK_META_KEYS}
    if isinstance(value, list):
        return [_clean(v) for v in value]
    return _strip_quotes(value)


@dataclass
class TerraformResource:
    type: str
    name: str
    attributes: dict[str, Any]
    source_file: str

    @property
    def is_azurerm(self) -> bool:
        return self.type.startswith("azurerm_")


@dataclass
class CollectorResult:
    repo_path: str
    files_scanned: int
    files_parsed: int
    parse_errors: list[dict[str, str]] = field(default_factory=list)
    resources: list[TerraformResource] = field(default_factory=list)

    @property
    def azurerm_resource_types(self) -> list[str]:
        return sorted({r.type for r in self.resources if r.is_azurerm})

    def by_type(self) -> dict[str, list[TerraformResource]]:
        out: dict[str, list[TerraformResource]] = {}
        for r in self.resources:
            out.setdefault(r.type, []).append(r)
        return out


def collect_terraform(repo_path: str | Path) -> CollectorResult:
    repo = Path(repo_path)
    if not repo.exists():
        raise FileNotFoundError(f"repo_path no existe: {repo}")

    tf_files = sorted(repo.rglob("*.tf"))
    parse_errors: list[dict[str, str]] = []
    resources: list[TerraformResource] = []
    parsed_count = 0

    for tf in tf_files:
        try:
            with tf.open(encoding="utf-8") as fp:
                doc = hcl2.load(fp)
            parsed_count += 1
        except Exception as exc:
            parse_errors.append({"file": str(tf), "error": f"{type(exc).__name__}: {exc}"})
            continue

        for block in doc.get("resource", []) or []:
            if not isinstance(block, dict):
                continue
            for raw_type, body in block.items():
                rtype = _strip_quotes(raw_type)
                if not isinstance(body, dict):
                    continue
                for raw_name, attrs in body.items():
                    rname = _strip_quotes(raw_name)
                    if rname in _BLOCK_META_KEYS or not isinstance(attrs, dict):
                        continue
                    resources.append(
                        TerraformResource(
                            type=rtype,
                            name=rname,
                            attributes=_clean(attrs),
                            source_file=str(tf),
                        )
                    )

    return CollectorResult(
        repo_path=str(repo),
        files_scanned=len(tf_files),
        files_parsed=parsed_count,
        parse_errors=parse_errors,
        resources=resources,
    )
