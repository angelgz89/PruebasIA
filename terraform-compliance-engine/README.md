# terraform-compliance-engine

Herramienta de análisis de seguridad para repositorios Terraform de Azure, integrable con GitHub Actions.

## Estado actual: MVP step 1 — "hola mundo"

El primer hito hace lo mínimo posible end-to-end:

1. Llama a un modelo de OpenAI (`gpt-5.4-pro` por defecto, soporta Azure OpenAI) con un prompt "hola mundo".
2. Parsea la respuesta.
3. Publica/actualiza un comentario en el PR de GitHub Actions.

Iremos añadiendo el resto del pipeline (lectura de `.tf`, mapping de controles, evaluación de frameworks, etc.) en pasos sucesivos.

## Estructura

```
terraform-compliance-engine/
├── engine/
│   └── run.py                  # Script principal del MVP
├── scripts/
│   └── dry_run_pr.py           # Harness local: simula un PR ficticio sin llamar a la API real de GitHub
├── .github/
│   └── workflows/
│       └── compliance.yml      # Workflow que se ejecuta en cada PR
├── requirements.txt
├── .env.example
├── .gitignore
└── README.md
```

## Uso local

```bash
pip install -r requirements.txt
cp .env.example .env  # rellena OPENAI_API_KEY (+ AZURE_OPENAI_ENDPOINT si usas Azure)
set -a && . ./.env && set +a
python engine/run.py
```

Sin variables de GitHub Actions presentes el script imprime la respuesta y se salta la publicación del comentario.

Para simular un PR ficticio end-to-end (LLM real + GitHub API mockeada):

```bash
python scripts/dry_run_pr.py
```

## Uso en GitHub Actions

### Secrets (Settings → Secrets and variables → Actions → Secrets)

| Nombre | Obligatorio | Descripción |
|---|---|---|
| `OPENAI_API_KEY` | Sí | API key de OpenAI público o de Azure OpenAI |

`GITHUB_TOKEN` lo provee Actions automáticamente — no hay que configurarlo.

### Variables (Settings → Secrets and variables → Actions → Variables)

Sólo necesarias si se usa Azure OpenAI. Si se omiten, el engine usa el cliente OpenAI público.

| Nombre                     | Ejemplo                            | Descripción                                             |
|----------------------------|------------------------------------|---------------------------------------------------------|
| `AZURE_OPENAI_ENDPOINT`    | `https://my-aoai.openai.azure.com` | Endpoint del recurso Azure OpenAI                       |
| `AZURE_OPENAI_API_VERSION` | `2025-04-01-preview`               | Versión de la API a usar                                |
| `OPENAI_MODEL`             | `gpt-5.4-pro`                      | Nombre del deployment (Azure) o modelo (OpenAI público) |

> Las **variables** son no-secretas y se pueden ver en logs; los endpoints y nombres de deployment no son sensibles. Sólo la API key va como **secret**.

### Disparadores

- `pull_request`: el workflow corre automáticamente en cada PR y comenta el resultado. El comentario se actualiza en cada push (busca por `<!-- compliance-report -->`) en lugar de duplicarse.
- `workflow_dispatch`: permite ejecutarlo manualmente desde la UI de Actions.

> **Nota sobre PRs desde forks**: con el evento `pull_request`, el `GITHUB_TOKEN` es read-only y no podrá publicar comentarios. Para repos públicos con forks habría que usar `pull_request_target` con cuidado. Para repos internos sin forks (caso actual), `pull_request` es la opción correcta y segura.

## Variables de entorno (referencia completa)

| Variable | Obligatoria | Descripción |
|---|---|---|
| `OPENAI_API_KEY` | Sí | API key de OpenAI o Azure OpenAI |
| `AZURE_OPENAI_ENDPOINT` | No | Si está definida, activa el cliente Azure OpenAI |
| `AZURE_OPENAI_API_VERSION` | No | Default `2025-04-01-preview` |
| `OPENAI_MODEL` | No | Default `gpt-5.4-pro` |
| `PROMPT` | No | Sobrescribe el prompt por defecto |
| `GITHUB_TOKEN` | Solo en Actions | Auto |
| `GITHUB_REPOSITORY` | Solo en Actions | Auto |
| `GITHUB_EVENT_PATH` | Solo en Actions | Auto |
