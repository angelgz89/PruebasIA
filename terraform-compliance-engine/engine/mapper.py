"""
Mapper — lookup determinista de controles por tipo de recurso.

Dado el resultado del collector (recursos detectados) y un master-mapping,
devuelve la lista plana de asignaciones (un par recurso ↔ control). Esta
fase NO usa LLM: su salida es 100% reproducible para un mismo input.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class ControlAssignment:
    control_id: str
    standard: str
    title: str
    severity: str
    families: list[str]
    evaluation_focus: str
    wiki_signals: str | None
    resource_type: str
    resource_name: str
    resource_attributes: dict[str, Any]
    source_file: str


@dataclass
class MappingResult:
    mapping_path: str
    mapping_metadata: dict[str, Any] = field(default_factory=dict)
    assignments: list[ControlAssignment] = field(default_factory=list)
    resources_without_controls: list[str] = field(default_factory=list)

    @property
    def total_assignments(self) -> int:
        return len(self.assignments)

    @property
    def assignments_by_standard(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for a in self.assignments:
            counts[a.standard] = counts.get(a.standard, 0) + 1
        return dict(sorted(counts.items()))


def load_mapping(mapping_path: str | Path) -> dict[str, Any]:
    p = Path(mapping_path)
    if not p.exists():
        raise FileNotFoundError(f"mapping_path no existe: {p}")
    with p.open(encoding="utf-8") as fp:
        data = yaml.safe_load(fp)
    if not isinstance(data, dict):
        raise ValueError(f"El master-mapping debe ser un dict YAML, recibido: {type(data).__name__}")
    return data


def resolve_controls(collector_result, mapping_path: str | Path) -> MappingResult:
    """Para cada recurso del collector busca en el mapping qué controles le
    aplican y devuelve la lista plana de asignaciones."""
    mapping = load_mapping(mapping_path)
    metadata = mapping.get("metadata", {}) or {}

    assignments: list[ControlAssignment] = []
    resources_without_controls: set[str] = set()

    for resource in collector_result.resources:
        rtype_block = mapping.get(resource.type)
        controls = (rtype_block or {}).get("controls") if isinstance(rtype_block, dict) else None
        if not controls:
            resources_without_controls.add(resource.type)
            continue

        for ctrl in controls:
            if not isinstance(ctrl, dict) or "id" not in ctrl:
                continue
            assignments.append(
                ControlAssignment(
                    control_id=ctrl["id"],
                    standard=ctrl.get("standard", "unknown"),
                    title=ctrl.get("title", ""),
                    severity=ctrl.get("severity", "medium"),
                    families=list(ctrl.get("families") or []),
                    evaluation_focus=(ctrl.get("evaluation_focus") or "").strip(),
                    wiki_signals=(ctrl.get("wiki_signals") or None),
                    resource_type=resource.type,
                    resource_name=resource.name,
                    resource_attributes=resource.attributes,
                    source_file=resource.source_file,
                )
            )

    return MappingResult(
        mapping_path=str(mapping_path),
        mapping_metadata=metadata,
        assignments=assignments,
        resources_without_controls=sorted(resources_without_controls),
    )
