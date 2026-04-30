# terraform-compliance-engine

Herramienta de análisis de seguridad para repositorios Terraform de Azure, integrable con GitHub Actions.

## Estado actual: MVP step 2 — Terraform collector

1. **Step 1**: llamada hello-world a un LLM (Azure OpenAI o OpenAI público) y publicación del resultado como comentario en el PR.
2. **Step 2**: lectura de los archivos `.tf` del repo con `python-hcl2` y extracción de los recursos `azurerm_*`. La lista se incluye en el comentario del PR.

Próximos pasos: master-mapping de controles por tipo de recurso, evaluación LLM control-a-control, soporte de wiki, reporte completo.

## Estructura

```
terraform-compliance-engine/
├── engine/
│   ├── run.py                  # Entry-point: orquesta collector + LLM + comentario
│   └── collector.py            # Lee .tf con python-hcl2, extrae recursos azurerm_*
├── scripts/
│   └── example-terraform/      # Storage account de ejemplo (con problemas intencionales)
├── .github/
│   └── workflows/
│       └── compliance.yml      # Workflow que se ejecuta en cada PR
├── requirements.txt
├── .gitignore
└── README.md
```

## Uso local

```bash
pip install -r requirements.txt
# crea un .env con OPENAI_API_KEY (+ AZURE_OPENAI_* si usas Azure)
set -a && . ./.env && set +a
python engine/run.py
```

Sin variables de GitHub Actions presentes, el script imprime el resultado por consola y se salta la publicación del comentario.

## Uso en GitHub Actions

### Secrets (Settings → Secrets and variables → Actions → Secrets)

| Nombre | Obligatorio | Descripción |
| --- | --- | --- |
| `OPENAI_API_KEY` | Sí | API key de OpenAI público o de Azure OpenAI |

`GITHUB_TOKEN` lo provee Actions automáticamente.

### Variables (Settings → Secrets and variables → Actions → Variables)

Sólo necesarias si se usa Azure OpenAI. Si se omiten, el engine usa el cliente OpenAI público.

| Nombre | Ejemplo | Descripción |
| --- | --- | --- |
| `AZURE_OPENAI_ENDPOINT` | `https://my-aoai.openai.azure.com` | Endpoint del recurso Azure OpenAI |
| `AZURE_OPENAI_API_VERSION` | `2025-04-01-preview` | Versión de la API a usar |
| `OPENAI_MODEL` | `gpt-5.4-pro` | Nombre del deployment (Azure) o modelo (OpenAI público) |

> Las **variables** son no-secretas y se pueden ver en logs; los endpoints y nombres de deployment no son sensibles. Sólo la API key va como **secret**.

### Disparadores

- `pull_request`: el workflow corre automáticamente en cada PR y comenta el resultado. El comentario se actualiza en cada push (busca por `<!-- compliance-report -->`) en lugar de duplicarse.
- `workflow_dispatch`: permite ejecutarlo manualmente desde la UI de Actions.

> **Nota sobre PRs desde forks**: con el evento `pull_request`, el `GITHUB_TOKEN` es read-only y no podrá publicar comentarios. Para repos públicos con forks habría que usar `pull_request_target` con cuidado. Para repos internos sin forks (caso actual), `pull_request` es la opción correcta y segura.

## Variables de entorno

| Variable | Obligatoria | Descripción |
| --- | --- | --- |
| `OPENAI_API_KEY` | Sí | API key de OpenAI o Azure OpenAI |
| `AZURE_OPENAI_ENDPOINT` | No | Si está definida, activa el cliente Azure OpenAI |
| `AZURE_OPENAI_API_VERSION` | No | Default `2025-04-01-preview` |
| `OPENAI_MODEL` | No | Default `gpt-5.4-pro` |
| `PROMPT` | No | Sobrescribe el prompt por defecto |
| `REPO_PATH` | No | Directorio con el Terraform a analizar (default `scripts/example-terraform`) |
| `GITHUB_TOKEN` | Solo en Actions | Auto |
| `GITHUB_REPOSITORY` | Solo en Actions | Auto |
| `GITHUB_EVENT_PATH` | Solo en Actions | Auto |
