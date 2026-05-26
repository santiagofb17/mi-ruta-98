# TikTok → Obsidian

Extrae los datos **públicos** de una cuenta de TikTok vía [Apify](https://apify.com)
y genera notas Markdown listas para tu vault de Obsidian (una nota de perfil + una por video,
con frontmatter YAML, métricas, hashtags y enlaces internos `[[...]]`).

## Requisitos

- Python 3.9+ (sin dependencias externas, solo la librería estándar).
- Una cuenta de Apify y tu **API token** (gratis para empezar):
  Apify Console → *Settings* → *Integrations* → *API token*.

## Uso

```bash
export APIFY_TOKEN="apify_api_xxxxxxxxxxxxxxxx"

python3 tiktok_to_obsidian.py <handle> -o "/ruta/a/tu/Vault" --limit 50
```

Ejemplo:

```bash
python3 tiktok_to_obsidian.py @cuenta_de_interes -o "$HOME/Obsidian/MiVault" --limit 100
```

Esto crea una carpeta `TikTok - @cuenta_de_interes/` dentro de tu vault con:

- `@cuenta_de_interes - perfil.md` — resumen del perfil (seguidores, likes, índice de videos).
- Una nota `.md` por cada video con sus métricas, descripción, hashtags y enlace.

## Opciones

| Flag | Descripción | Default |
|------|-------------|---------|
| `handle` | Usuario de TikTok (con o sin `@`). | — |
| `-o, --output` | Carpeta destino (tu vault de Obsidian). | requerido |
| `--limit` | Máximo de videos a extraer. | 50 |
| `--actor` | Actor de Apify a usar. | `clockworks~free-tiktok-scraper` |

## Notas legales

Esta herramienta solo accede a **información pública** y está pensada para archivo/consulta
personal. El scraping puede ir contra los Términos de Servicio de TikTok y de Apify; úsala de
forma responsable, respeta los límites de uso y no la apliques a datos privados ni personales
de terceros sin base legítima.
