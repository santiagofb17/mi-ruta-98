#!/usr/bin/env python3
"""Extrae una cuenta pública de TikTok vía Apify y genera notas Markdown para Obsidian.

Genera, dentro de tu vault:
    TikTok - @handle/
        @handle - perfil.md        (índice: videos en orden del perfil + playlists)
        Videos/<video>.md          (videos sin playlist, en orden del perfil)
        Playlists/<playlist>/<video>.md
        _raw.json                  (respuesta cruda de Apify, para verificar/afinar)

Cada nota de video incluye métricas, descripción, hashtags y el TRANSCRIPT ordenado.

Uso:
    export APIFY_TOKEN="tu_token"
    python3 tiktok_to_obsidian.py <handle> -o /ruta/a/tu/vault [--limit 50]
"""

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

APIFY_ACTOR = "clockworks~tiktok-scraper"
APIFY_URL = (
    "https://api.apify.com/v2/acts/{actor}/run-sync-get-dataset-items?token={token}"
)


def http_post_json(url, payload, timeout=600):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_from_apify(handle, token, limit, actor):
    payload = {
        "profiles": [handle],
        "resultsPerPage": limit,
        "profileSorting": "latest",
        "shouldDownloadSubtitles": True,
        "shouldDownloadVideos": False,
        "shouldDownloadCovers": False,
        "shouldDownloadSlideshowImages": False,
    }
    url = APIFY_URL.format(actor=actor, token=token)
    try:
        return http_post_json(url, payload)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        sys.exit(f"Error HTTP {e.code} de Apify: {body}")
    except urllib.error.URLError as e:
        sys.exit(f"Error de red al contactar Apify: {e.reason}")


# ----------------------------- helpers ------------------------------------ #

def first(d, *keys, default=None):
    """Devuelve el primer valor no vacío entre varias claves posibles."""
    for k in keys:
        if isinstance(d, dict) and d.get(k) not in (None, "", [], {}):
            return d[k]
    return default


def slugify(value):
    value = re.sub(r"[^\w\s-]", "", str(value), flags=re.UNICODE).strip().lower()
    return re.sub(r"[\s_-]+", "-", value) or "sin-titulo"


def yaml_escape(value):
    if value is None:
        return '""'
    s = str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")
    return f'"{s}"'


def author_from_items(items):
    for it in items:
        meta = it.get("authorMeta") or it.get("author")
        if isinstance(meta, dict) and meta:
            return meta
    return {}


def video_title(it):
    text = (it.get("text") or it.get("desc") or "").strip().replace("\n", " ")
    if text:
        return text[:80]
    return f"video-{it.get('id', 'sin-id')}"


def playlist_name(it):
    """Detecta el nombre de la playlist de un video con varias claves posibles."""
    pl = first(it, "playlistName", "playListName", "collectionName")
    if pl:
        return str(pl)
    nested = it.get("playlist") or it.get("playList") or it.get("collection")
    if isinstance(nested, dict):
        return str(first(nested, "name", "title", default="")) or None
    return None


def is_pinned(it):
    return bool(first(it, "isPinned", "pinned", default=False))


def create_ts(it):
    """Timestamp numérico para ordenar (mayor = más reciente)."""
    ts = it.get("createTime")
    if isinstance(ts, (int, float)):
        return ts
    iso = it.get("createTimeISO")
    if iso:
        try:
            return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
        except ValueError:
            pass
    return 0


def profile_order(items):
    """Orden del perfil: fijados primero, luego del más reciente al más antiguo."""
    return sorted(items, key=lambda it: (not is_pinned(it), -create_ts(it)))


# --------------------------- transcripts ----------------------------------- #

def subtitle_links(it):
    vmeta = it.get("videoMeta") or {}
    links = first(vmeta, "subtitleLinks", default=None) or first(
        it, "subtitleLinks", default=None
    )
    return links if isinstance(links, list) else []


def parse_vtt_or_srt(text):
    """Convierte WebVTT/SRT en texto plano ordenado, sin duplicados consecutivos."""
    out = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.upper().startswith("WEBVTT"):
            continue
        if "-->" in line:
            continue
        if re.fullmatch(r"\d+", line):  # índice de cue (SRT)
            continue
        if line.upper().startswith(("NOTE", "STYLE", "REGION")):
            continue
        clean = re.sub(r"<[^>]+>", "", line).strip()  # tags <c>, <00:00:00.000>
        if clean and (not out or out[-1] != clean):
            out.append(clean)
    return "\n".join(out)


def fetch_transcript(it):
    links = subtitle_links(it)
    if not links:
        return None, None
    # Prioriza inglés/original; si no, toma el primero.
    def lang_of(l):
        return str(first(l, "language", "languageCodeName", "source", default="")).lower()

    links_sorted = sorted(
        links, key=lambda l: (0 if lang_of(l).startswith("en") else 1)
    )
    for link in links_sorted:
        url = first(link, "downloadLink", "link", "url")
        if not url:
            continue
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=60) as resp:
                raw = resp.read().decode("utf-8", "replace")
            transcript = parse_vtt_or_srt(raw)
            if transcript:
                return transcript, lang_of(link) or "desconocido"
        except Exception:
            continue
    return None, None


# ----------------------------- writers ------------------------------------- #

def write_video_note(path, handle_name, it, group_label):
    title = video_title(it)
    vmeta = it.get("videoMeta") or {}
    music = it.get("musicMeta") or {}
    hashtags = [
        h.get("name")
        for h in (it.get("hashtags") or [])
        if isinstance(h, dict) and h.get("name")
    ]
    transcript, tlang = fetch_transcript(it)

    fm = {
        "tipo": "tiktok-video",
        "cuenta": f"@{handle_name}",
        "playlist": group_label,
        "id": it.get("id"),
        "fecha": it.get("createTimeISO") or it.get("createTime"),
        "fijado": is_pinned(it),
        "vistas": it.get("playCount"),
        "likes": it.get("diggCount"),
        "comentarios": it.get("commentCount"),
        "compartidos": it.get("shareCount"),
        "guardados": it.get("collectCount"),
        "duracion_seg": vmeta.get("duration"),
        "transcript_idioma": tlang,
        "url": it.get("webVideoUrl"),
    }
    lines = ["---"]
    for k, v in fm.items():
        lines.append(f"{k}: {yaml_escape(v)}")
    tag_list = ", ".join(["tiktok", "video"] + [slugify(h) for h in hashtags])
    lines.append(f"tags: [{tag_list}]")
    lines.append("---\n")
    lines.append(f"# {title}\n")
    lines.append(f"Cuenta: [[@{handle_name} - perfil|@{handle_name}]] · Playlist: {group_label}\n")

    full_text = (it.get("text") or it.get("desc") or "").strip()
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

    lines.append("## Transcript\n")
    if transcript:
        lines.append(transcript + "\n")
    else:
        lines.append("_(Sin subtítulos/transcript disponible para este video.)_\n")

    if hashtags:
        lines.append("## Hashtags\n")
        lines.append(" ".join(f"#{h}" for h in hashtags) + "\n")

    if music.get("musicName"):
        lines.append("## Audio\n")
        lines.append(f"{music.get('musicName')} — {music.get('musicAuthor', '')}\n")

    if fm["url"]:
        lines.append(f"[Ver en TikTok]({fm['url']})\n")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def write_profile_note(out_dir, handle, author, ordered, groups):
    name = author.get("name") or author.get("uniqueId") or handle
    nick = author.get("nickName") or author.get("nickname") or name
    fm = {
        "tipo": "tiktok-perfil",
        "handle": f"@{name}",
        "nombre": nick,
        "seguidores": first(author, "fans", "followerCount"),
        "siguiendo": first(author, "following", "followingCount"),
        "likes_totales": first(author, "heart", "heartCount"),
        "videos_totales": first(author, "video", "videoCount"),
        "verificado": author.get("verified"),
        "url": f"https://www.tiktok.com/@{name}",
        "extraido": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    }
    lines = ["---"]
    for k, v in fm.items():
        lines.append(f"{k}: {yaml_escape(v)}")
    lines.append("tags: [tiktok, archivo, perfil]")
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
    lines.append(f"| Videos extraídos | {len(ordered)} |")
    lines.append(f"| Playlists | {len([g for g in groups if g != 'Videos'])} |\n")

    lines.append("## Videos (orden del perfil)\n")
    for it in ordered:
        pin = "📌 " if is_pinned(it) else ""
        t = video_title(it)
        lines.append(f"1. {pin}[[{slugify(t)}|{t}]]")
    lines.append("")

    lines.append("## Playlists\n")
    for label in sorted(groups):
        if label == "Videos":
            continue
        lines.append(f"### {label}\n")
        for it in groups[label]:
            t = video_title(it)
            lines.append(f"- [[{slugify(t)}|{t}]]")
        lines.append("")
    if "Videos" in groups:
        lines.append("### (Sin playlist)\n")
        for it in groups["Videos"]:
            t = video_title(it)
            lines.append(f"- [[{slugify(t)}|{t}]]")
        lines.append("")

    path = os.path.join(out_dir, f"@{name} - perfil.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return name


# ------------------------------- main -------------------------------------- #

def main():
    parser = argparse.ArgumentParser(
        description="Extrae una cuenta pública de TikTok vía Apify a notas de Obsidian."
    )
    parser.add_argument("handle", help="Usuario de TikTok (con o sin @).")
    parser.add_argument("-o", "--output", required=True, help="Carpeta destino (tu vault).")
    parser.add_argument("--limit", type=int, default=50, help="Máximo de videos (default 50).")
    parser.add_argument("--actor", default=APIFY_ACTOR, help="Actor de Apify a usar.")
    args = parser.parse_args()

    token = os.environ.get("APIFY_TOKEN")
    if not token:
        sys.exit("Falta APIFY_TOKEN. Exporta tu token: export APIFY_TOKEN='...'")

    handle = args.handle.lstrip("@").strip()
    base = os.path.join(args.output, f"TikTok - @{handle}")
    os.makedirs(base, exist_ok=True)

    print(f"Extrayendo @{handle} (hasta {args.limit} videos, con subtítulos) vía Apify…")
    items = fetch_from_apify(handle, token, args.limit, args.actor)
    if not isinstance(items, list) or not items:
        sys.exit("Apify no devolvió datos. Revisa el handle, tu token o el saldo de la cuenta.")

    with open(os.path.join(base, "_raw.json"), "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)

    videos = [it for it in items if isinstance(it, dict) and (it.get("id") or it.get("text"))]
    author = author_from_items(videos)
    ordered = profile_order(videos)

    # Agrupa por playlist (en orden del perfil dentro de cada grupo).
    groups = {}
    for it in ordered:
        label = playlist_name(it) or "Videos"
        groups.setdefault(label, []).append(it)

    name = write_profile_note(base, handle, author, ordered, groups)

    for label, vids in groups.items():
        if label == "Videos":
            folder = os.path.join(base, "Videos")
        else:
            folder = os.path.join(base, "Playlists", slugify(label))
        os.makedirs(folder, exist_ok=True)
        for it in vids:
            note_path = os.path.join(folder, f"{slugify(video_title(it))}.md")
            write_video_note(note_path, name, it, label)

    n_pl = len([g for g in groups if g != "Videos"])
    print(
        f"Listo. {len(videos)} videos en orden de perfil, {n_pl} playlists, "
        f"transcripts incluidos.\nCarpeta: {base}"
    )


if __name__ == "__main__":
    main()
