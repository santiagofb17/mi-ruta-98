#!/usr/bin/env python3
"""Archiva una cuenta pública de TikTok en Obsidian usando yt-dlp (gratis, sin API).

Genera, dentro de tu vault:
    TikTok - @handle/
        @handle - perfil.md            (índice: videos en orden + playlists)
        Videos/<video>.md              (videos del perfil, del más nuevo al más viejo)
        Playlists/<playlist>/<video>.md (solo si pasas --playlist "Nombre=URL")

Cada nota incluye métricas, descripción, hashtags y el TRANSCRIPT ordenado
(cuando el video tiene subtítulos disponibles).

Requisitos:
    - Python 3.9+
    - yt-dlp instalado:  python3 -m pip install -U yt-dlp

Uso:
    python3 tiktok_to_obsidian.py @ernietheplutus -o "/ruta/a/tu/Vault" --limit 50

Con playlists separadas (copia las URLs desde la pestaña "Playlists" del perfil):
    python3 tiktok_to_obsidian.py @ernietheplutus -o "/ruta/a/tu/Vault" \
        --playlist "Recetas=https://www.tiktok.com/@ernietheplutus/playlist/Recetas-1234" \
        --playlist "Tips=https://www.tiktok.com/@ernietheplutus/playlist/Tips-5678"
"""

import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone


def ensure_ytdlp(ytdlp):
    if shutil.which(ytdlp) is None:
        sys.exit(
            f"No encuentro '{ytdlp}'. Instálalo con:\n"
            f"    python3 -m pip install -U yt-dlp"
        )


def run_ytdlp(ytdlp, url, limit, tmpdir):
    """Descarga metadatos + subtítulos (sin el video) a tmpdir."""
    cmd = [
        ytdlp,
        url,
        "--skip-download",
        "--write-info-json",
        "--write-subs",
        "--write-auto-subs",
        "--sub-langs", "all",
        "--sub-format", "vtt/srt/best",
        "--ignore-errors",
        "--no-warnings",
        "--no-write-playlist-metafiles",
        "-I", f"1:{limit}",
        "-o", os.path.join(tmpdir, "%(id)s.%(ext)s"),
    ]
    print(f"  yt-dlp → {url}")
    subprocess.run(cmd, check=False)


# ----------------------------- helpers ------------------------------------ #

def slugify(value):
    value = re.sub(r"[^\w\s-]", "", str(value), flags=re.UNICODE).strip().lower()
    return re.sub(r"[\s_-]+", "-", value) or "sin-titulo"


def yaml_escape(value):
    if value is None:
        return '""'
    s = str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")
    return f'"{s}"'


def load_videos(tmpdir):
    """Carga los *.info.json (un objeto por video), ignorando metafiles de playlist."""
    out = []
    for path in sorted(glob.glob(os.path.join(tmpdir, "*.info.json"))):
        try:
            with open(path, encoding="utf-8") as f:
                info = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(info, dict) or info.get("_type") == "playlist":
            continue
        if not info.get("id"):
            continue
        out.append(info)
    return out


def video_title(info):
    text = (info.get("description") or info.get("title") or "").strip().replace("\n", " ")
    if text:
        return text[:80]
    return f"video-{info.get('id', 'sin-id')}"


def video_ts(info):
    ts = info.get("timestamp")
    return ts if isinstance(ts, (int, float)) else 0


def hashtags_from(info):
    tags = info.get("tags")
    if isinstance(tags, list) and tags:
        return [str(t) for t in tags]
    desc = info.get("description") or ""
    return re.findall(r"#(\w+)", desc)


# --------------------------- transcripts ----------------------------------- #

def parse_vtt_or_srt(text):
    out = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.upper().startswith("WEBVTT"):
            continue
        if "-->" in line or re.fullmatch(r"\d+", line):
            continue
        if line.upper().startswith(("NOTE", "STYLE", "REGION", "KIND:", "LANGUAGE:")):
            continue
        clean = re.sub(r"<[^>]+>", "", line).strip()
        clean = re.sub(r"&nbsp;?", " ", clean)
        if clean and (not out or out[-1] != clean):
            out.append(clean)
    return "\n".join(out)


def find_transcript(tmpdir, video_id):
    """Busca un archivo de subtítulos para el video y lo parsea. Prioriza inglés."""
    candidates = glob.glob(os.path.join(tmpdir, f"{video_id}.*.vtt")) + glob.glob(
        os.path.join(tmpdir, f"{video_id}.*.srt")
    )
    if not candidates:
        return None, None
    candidates.sort(key=lambda p: (0 if ".en" in os.path.basename(p).lower() else 1))
    for path in candidates:
        try:
            with open(path, encoding="utf-8") as f:
                text = parse_vtt_or_srt(f.read())
        except OSError:
            continue
        if text:
            lang = os.path.basename(path).split(".")[-2]
            return text, lang
    return None, None


# ----------------------------- writers ------------------------------------- #

def write_video_note(path, handle, info, group_label, tmpdir):
    title = video_title(info)
    hashtags = hashtags_from(info)
    transcript, tlang = find_transcript(tmpdir, info.get("id"))
    fm = {
        "tipo": "tiktok-video",
        "cuenta": f"@{handle}",
        "playlist": group_label,
        "id": info.get("id"),
        "fecha": datetime.fromtimestamp(video_ts(info), timezone.utc).strftime("%Y-%m-%d")
        if video_ts(info)
        else info.get("upload_date"),
        "vistas": info.get("view_count"),
        "likes": info.get("like_count"),
        "comentarios": info.get("comment_count"),
        "compartidos": info.get("repost_count"),
        "duracion_seg": info.get("duration"),
        "transcript_idioma": tlang,
        "url": info.get("webpage_url"),
    }
    lines = ["---"]
    for k, v in fm.items():
        lines.append(f"{k}: {yaml_escape(v)}")
    tag_list = ", ".join(["tiktok", "video"] + [slugify(h) for h in hashtags])
    lines.append(f"tags: [{tag_list}]")
    lines.append("---\n")
    lines.append(f"# {title}\n")
    lines.append(f"Cuenta: [[@{handle} - perfil|@{handle}]] · Playlist: {group_label}\n")

    desc = (info.get("description") or "").strip()
    if desc:
        lines.append("## Descripción\n")
        lines.append(desc + "\n")

    lines.append("## Métricas\n")
    lines.append("| Métrica | Valor |")
    lines.append("|---|---|")
    lines.append(f"| Vistas | {fm['vistas']} |")
    lines.append(f"| Likes | {fm['likes']} |")
    lines.append(f"| Comentarios | {fm['comentarios']} |")
    lines.append(f"| Compartidos | {fm['compartidos']} |")
    lines.append(f"| Duración (seg) | {fm['duracion_seg']} |\n")

    lines.append("## Transcript\n")
    lines.append((transcript + "\n") if transcript else
                 "_(Sin subtítulos/transcript disponible para este video.)_\n")

    if hashtags:
        lines.append("## Hashtags\n")
        lines.append(" ".join(f"#{h}" for h in hashtags) + "\n")

    if fm["url"]:
        lines.append(f"[Ver en TikTok]({fm['url']})\n")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def write_profile_note(base, handle, sample, profile_videos, playlists):
    follower = sample.get("channel_follower_count") if sample else None
    nick = (sample.get("uploader") or sample.get("channel") or handle) if sample else handle
    fm = {
        "tipo": "tiktok-perfil",
        "handle": f"@{handle}",
        "nombre": nick,
        "seguidores": follower,
        "videos_extraidos": len(profile_videos) + sum(len(v) for v in playlists.values()),
        "url": f"https://www.tiktok.com/@{handle}",
        "extraido": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    }
    lines = ["---"]
    for k, v in fm.items():
        lines.append(f"{k}: {yaml_escape(v)}")
    lines.append("tags: [tiktok, archivo, perfil]")
    lines.append("---\n")
    lines.append(f"# {nick} (@{handle})\n")

    lines.append("## Videos (orden del perfil)\n")
    for info in profile_videos:
        t = video_title(info)
        lines.append(f"1. [[{slugify(t)}|{t}]]")
    lines.append("")

    if playlists:
        lines.append("## Playlists\n")
        for label in sorted(playlists):
            lines.append(f"### {label}\n")
            for info in playlists[label]:
                t = video_title(info)
                lines.append(f"- [[{slugify(t)}|{t}]]")
            lines.append("")

    with open(os.path.join(base, f"@{handle} - perfil.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


# ------------------------------- main -------------------------------------- #

def parse_playlist_args(values):
    playlists = {}
    for v in values or []:
        if "=" not in v:
            sys.exit(f"--playlist debe ser 'Nombre=URL', recibí: {v}")
        name, url = v.split("=", 1)
        playlists[name.strip()] = url.strip()
    return playlists


def collect(ytdlp, url, limit):
    """Corre yt-dlp en un tmpdir y devuelve (videos_ordenados, tmpdir)."""
    tmpdir = tempfile.mkdtemp(prefix="ttdl_")
    run_ytdlp(ytdlp, url, limit, tmpdir)
    videos = load_videos(tmpdir)
    videos.sort(key=video_ts, reverse=True)  # más reciente primero (orden del perfil)
    return videos, tmpdir


def main():
    parser = argparse.ArgumentParser(
        description="Archiva una cuenta pública de TikTok en Obsidian usando yt-dlp (gratis)."
    )
    parser.add_argument("handle", help="Usuario de TikTok (con o sin @).")
    parser.add_argument("-o", "--output", required=True, help="Carpeta destino (tu vault).")
    parser.add_argument("--limit", type=int, default=50, help="Máximo de videos (default 50).")
    parser.add_argument(
        "--playlist", action="append", metavar="Nombre=URL",
        help="Playlist a separar (repetible). Copia la URL desde el perfil.",
    )
    parser.add_argument("--ytdlp", default="yt-dlp", help="Ruta al binario de yt-dlp.")
    args = parser.parse_args()

    ensure_ytdlp(args.ytdlp)
    handle = args.handle.lstrip("@").strip()
    playlists_urls = parse_playlist_args(args.playlist)

    base = os.path.join(args.output, f"TikTok - @{handle}")
    os.makedirs(base, exist_ok=True)
    tmpdirs = []
    sample = None

    print(f"Archivando @{handle} con yt-dlp (hasta {args.limit} por fuente)…")

    # 1) Perfil completo (orden cronológico).
    profile_url = f"https://www.tiktok.com/@{handle}"
    profile_videos, tmp = collect(args.ytdlp, profile_url, args.limit)
    tmpdirs.append(tmp)
    if not profile_videos:
        shutil.rmtree(tmp, ignore_errors=True)
        sys.exit(
            "yt-dlp no devolvió videos. Verifica el handle, tu conexión, o actualiza yt-dlp\n"
            "    python3 -m pip install -U yt-dlp"
        )
    sample = profile_videos[0]

    # 2) Playlists (cada una en su carpeta).
    playlists = {}
    playlist_tmp = {}
    for name, url in playlists_urls.items():
        vids, t = collect(args.ytdlp, url, args.limit)
        tmpdirs.append(t)
        playlists[name] = vids
        playlist_tmp[name] = t

    # 3) Escribe notas.
    write_profile_note(base, handle, sample, profile_videos, playlists)

    videos_dir = os.path.join(base, "Videos")
    os.makedirs(videos_dir, exist_ok=True)
    for info in profile_videos:
        note = os.path.join(videos_dir, f"{slugify(video_title(info))}.md")
        write_video_note(note, handle, info, "Videos", tmp)

    for name, vids in playlists.items():
        folder = os.path.join(base, "Playlists", slugify(name))
        os.makedirs(folder, exist_ok=True)
        for info in vids:
            note = os.path.join(folder, f"{slugify(video_title(info))}.md")
            write_video_note(note, handle, info, name, playlist_tmp[name])

    for t in tmpdirs:
        shutil.rmtree(t, ignore_errors=True)

    print(
        f"Listo. {len(profile_videos)} videos del perfil, "
        f"{len(playlists)} playlists, transcripts incluidos.\nCarpeta: {base}"
    )


if __name__ == "__main__":
    main()
