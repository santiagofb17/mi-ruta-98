#!/usr/bin/env python3
"""Extrae una cuenta pública de TikTok vía Apify y genera notas Markdown para Obsidian.

Uso:
    export APIFY_TOKEN="tu_token"
    python3 tiktok_to_obsidian.py <handle> -o /ruta/a/tu/vault [--limit 50]

El <handle> puede ir con o sin '@' (ej. "restaurantesilvestre" o "@restaurantesilvestre").
"""

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

APIFY_ACTOR = "clockworks~free-tiktok-scraper"
APIFY_URL = (
    "https://api.apify.com/v2/acts/{actor}/run-sync-get-dataset-items?token={token}"
)


def fetch_from_apify(handle, token, limit, actor):
    """Llama al actor de Apify y devuelve la lista de items (videos) del perfil."""
    payload = {
        "profiles": [handle],
        "resultsPerPage": limit,
        "shouldDownloadVideos": False,
        "shouldDownloadCovers": False,
        "shouldDownloadSubtitles": False,
        "shouldDownloadSlideshowImages": False,
    }
    url = APIFY_URL.format(actor=actor, token=token)
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        sys.exit(f"Error HTTP {e.code} de Apify: {body}")
    except urllib.error.URLError as e:
        sys.exit(f"Error de red al contactar Apify: {e.reason}")


def slugify(value):
    value = re.sub(r"[^\w\s-]", "", str(value), flags=re.UNICODE).strip().lower()
    return re.sub(r"[\s_-]+", "-", value) or "sin-titulo"


def yaml_escape(value):
    """Devuelve un valor seguro para frontmatter YAML."""
    if value is None:
        return '""'
    s = str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")
    return f'"{s}"'


def author_from_items(items):
    """Extrae el perfil del autor del primer item que lo contenga."""
    for it in items:
        meta = it.get("authorMeta") or it.get("author")
        if isinstance(meta, dict) and meta:
            return meta
    return {}


def write_profile_note(out_dir, handle, author, items):
    name = author.get("name") or author.get("uniqueId") or handle
    nick = author.get("nickName") or author.get("nickname") or name
    fm = {
        "tipo": "tiktok-perfil",
        "handle": f"@{name}",
        "nombre": nick,
        "seguidores": author.get("fans") or author.get("followerCount"),
        "siguiendo": author.get("following") or author.get("followingCount"),
        "likes_totales": author.get("heart") or author.get("heartCount"),
        "videos_totales": author.get("video") or author.get("videoCount"),
        "verificado": author.get("verified"),
        "url": f"https://www.tiktok.com/@{name}",
        "extraido": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    }
    lines = ["---"]
    for k, v in fm.items():
        lines.append(f"{k}: {yaml_escape(v)}")
    lines.append("tags: [tiktok, archivo]")
    lines.append("---\n")
    lines.append(f"# {nick} (@{name})\n")

    bio = author.get("signature") or ""
    if bio:
        lines.append(f"> {bio}\n")

    lines.append("## Resumen\n")
    lines.append("| Métrica | Valor |")
    lines.append("|---|---|")
    lines.append(f"| Seguidores | {fm['seguidores']} |")
    lines.append(f"| Siguiendo | {fm['siguiendo']} |")
    lines.append(f"| Likes totales | {fm['likes_totales']} |")
    lines.append(f"| Videos publicados | {fm['videos_totales']} |")
    lines.append(f"| Videos extraídos | {len(items)} |")
    lines.append(f"| Verificado | {fm['verificado']} |\n")

    lines.append("## Videos extraídos\n")
    for it in items:
        title = video_title(it)
        lines.append(f"- [[{slugify(title)}|{title}]]")
    lines.append("")

    path = os.path.join(out_dir, f"@{name} - perfil.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return name


def video_title(it):
    text = (it.get("text") or "").strip().replace("\n", " ")
    if text:
        return text[:80]
    return f"video-{it.get('id', 'sin-id')}"


def write_video_note(out_dir, handle_name, it):
    title = video_title(it)
    created = it.get("createTimeISO") or it.get("createTime") or ""
    vmeta = it.get("videoMeta") or {}
    music = it.get("musicMeta") or {}
    hashtags = [
        h.get("name") for h in (it.get("hashtags") or []) if isinstance(h, dict) and h.get("name")
    ]
    fm = {
        "tipo": "tiktok-video",
        "cuenta": f"@{handle_name}",
        "id": it.get("id"),
        "fecha": created,
        "vistas": it.get("playCount"),
        "likes": it.get("diggCount"),
        "comentarios": it.get("commentCount"),
        "compartidos": it.get("shareCount"),
        "guardados": it.get("collectCount"),
        "duracion_seg": vmeta.get("duration"),
        "url": it.get("webVideoUrl"),
    }
    lines = ["---"]
    for k, v in fm.items():
        lines.append(f"{k}: {yaml_escape(v)}")
    tag_list = ", ".join(["tiktok", "video"] + [slugify(h) for h in hashtags])
    lines.append(f"tags: [{tag_list}]")
    lines.append("---\n")
    lines.append(f"# {title}\n")
    lines.append(f"Cuenta: [[@{handle_name} - perfil|@{handle_name}]]\n")

    full_text = (it.get("text") or "").strip()
    if full_text:
        lines.append("## Descripción\n")
        lines.append(full_text + "\n")

    lines.append("## Métricas\n")
    lines.append("| Métrica | Valor |")
    lines.append("|---|---|")
    lines.append(f"| Vistas | {fm['vistas']} |")
    lines.append(f"| Likes | {fm['likes']} |")
    lines.append(f"| Comentarios | {fm['comentarios']} |")
    lines.append(f"| Compartidos | {fm['compartidos']} |")
    lines.append(f"| Guardados | {fm['guardados']} |")
    lines.append(f"| Duración (seg) | {fm['duracion_seg']} |\n")

    if hashtags:
        lines.append("## Hashtags\n")
        lines.append(" ".join(f"#{h}" for h in hashtags) + "\n")

    if music.get("musicName"):
        lines.append("## Audio\n")
        lines.append(f"{music.get('musicName')} — {music.get('musicAuthor', '')}\n")

    if fm["url"]:
        lines.append(f"[Ver en TikTok]({fm['url']})\n")

    path = os.path.join(out_dir, f"{slugify(title)}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def main():
    parser = argparse.ArgumentParser(
        description="Extrae una cuenta pública de TikTok vía Apify a notas de Obsidian."
    )
    parser.add_argument("handle", help="Usuario de TikTok (con o sin @).")
    parser.add_argument(
        "-o", "--output", required=True, help="Carpeta destino dentro de tu vault de Obsidian."
    )
    parser.add_argument(
        "--limit", type=int, default=50, help="Máximo de videos a extraer (default: 50)."
    )
    parser.add_argument("--actor", default=APIFY_ACTOR, help="Actor de Apify a usar.")
    args = parser.parse_args()

    token = os.environ.get("APIFY_TOKEN")
    if not token:
        sys.exit("Falta APIFY_TOKEN. Exporta tu token: export APIFY_TOKEN='...'")

    handle = args.handle.lstrip("@").strip()
    out_dir = os.path.join(args.output, f"TikTok - @{handle}")
    os.makedirs(out_dir, exist_ok=True)

    print(f"Extrayendo @{handle} (hasta {args.limit} videos) vía Apify…")
    items = fetch_from_apify(handle, token, args.limit, args.actor)
    if not isinstance(items, list) or not items:
        sys.exit("Apify no devolvió datos. Revisa el handle, tu token o el saldo de la cuenta.")

    # Algunos items pueden ser errores del actor; filtra los que no son videos.
    videos = [it for it in items if isinstance(it, dict) and (it.get("id") or it.get("text"))]
    author = author_from_items(videos)

    name = write_profile_note(out_dir, handle, author, videos)
    for it in videos:
        write_video_note(out_dir, name, it)

    print(f"Listo. {len(videos)} videos + 1 nota de perfil en: {out_dir}")


if __name__ == "__main__":
    main()
