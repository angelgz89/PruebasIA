# PruebasIA

Sandbox para experimentos con LLMs e infraestructura como código.

## Contenido

- [`terraform-compliance-engine/`](terraform-compliance-engine/) — herramienta de análisis de seguridad para repos Terraform de Azure, integrable con GitHub Actions. Estado actual: MVP step 1 (hello-world LLM con comentario en PR).
- [`MCSB/`](MCSB/), [`PolicySetGenerator/`](PolicySetGenerator/) — material auxiliar.

## Workflow de compliance

El workflow `.github/workflows/compliance.yml` se ejecuta automáticamente en cada PR y publica un comentario con el resultado de la llamada al LLM. Para que funcione, en *Settings → Secrets and variables → Actions* tiene que estar configurado el secret `OPENAI_API_KEY` y, si se usa Azure OpenAI, las variables `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_API_VERSION` y `OPENAI_MODEL`.
