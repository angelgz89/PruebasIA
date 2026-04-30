# Smoke test: security-check label

Marcador del PR de smoke test del filtro opt-in `security-check`.
Generado: 2026-04-30.

Comportamiento esperado:
- PR sin label: el workflow `compliance` queda `skipped`.
- PR con label `security-check`: el workflow se ejecuta.
