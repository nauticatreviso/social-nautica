#!/usr/bin/env python3
"""Converte automaticamente i video orizzontali in formato verticale Reel (9:16).

Controlla tutti i file .mp4 in media/ referenziati da posts.json.
Se un video è orizzontale (larghezza > altezza), lo converte in 1080x1920
con crop centrale e sovrascrive il file originale.

Pensato per girare su GitHub Actions prima della pubblicazione.
Ubuntu-latest ha già ffmpeg installato.
"""
import json
import subprocess
import sys
from pathlib import Path

POSTS_FILE = Path("posts.json")
MEDIA_DIR = Path("media")

# Formato target: Instagram Reels
TARGET_W = 1080
TARGET_H = 1920


def get_dimensions(path):
    """Restituisce (width, height) del video usando ffprobe."""
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=width,height",
                "-of", "csv=s=x:p=0",
                str(path),
            ],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            return None, None
        parts = [p for p in result.stdout.strip().split("x") if p]
        if len(parts) >= 2:
            return int(parts[0]), int(parts[1])
    except Exception as e:
        print(f"  [ERRORE] ffprobe fallito per {path}: {e}", file=sys.stderr)
    return None, None


def convert_to_vertical(path):
    """Converte un video orizzontale in verticale 1080x1920 con crop centrale."""
    tmp = path.with_suffix(".tmp.mp4")
    cmd = [
        "ffmpeg", "-y", "-i", str(path),
        "-vf", "crop=ih*9/16:ih:(iw-ih*9/16)/2:0,scale=1080:1920",
        "-c:a", "copy",
        "-movflags", "+faststart",
        str(tmp),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode != 0:
            print(f"  [ERRORE] ffmpeg fallito:\n{result.stderr[-500:]}", file=sys.stderr)
            tmp.unlink(missing_ok=True)
            return False
        # Sostituisci l'originale con il file convertito
        tmp.replace(path)
        return True
    except Exception as e:
        print(f"  [ERRORE] Conversione fallita: {e}", file=sys.stderr)
        tmp.unlink(missing_ok=True)
        return False


def scale_to_vertical(path):
    """Scala un video già verticale a 1080x1920."""
    tmp = path.with_suffix(".tmp.mp4")
    cmd = [
        "ffmpeg", "-y", "-i", str(path),
        "-vf", "scale=1080:1920",
        "-c:a", "copy",
        "-movflags", "+faststart",
        str(tmp),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode != 0:
            print(f"  [ERRORE] ffmpeg scale fallito:\n{result.stderr[-500:]}", file=sys.stderr)
            tmp.unlink(missing_ok=True)
            return False
        tmp.replace(path)
        return True
    except Exception as e:
        print(f"  [ERRORE] Scale fallita: {e}", file=sys.stderr)
        tmp.unlink(missing_ok=True)
        return False


def main():
    if not POSTS_FILE.exists():
        print("posts.json non trovato, nulla da fare.")
        return

    posts = json.loads(POSTS_FILE.read_text(encoding="utf-8"))

    # Trova tutti i video referenziati dai post
    video_files = set()
    for post in posts:
        if post.get("type") in ("video", "reel"):
            media = post.get("media", "")
            if media and not media.startswith("http"):
                video_files.add(Path(media))

    converted = 0
    skipped = 0
    errors = 0

    for vf in sorted(video_files):
        if not vf.exists():
            continue

        w, h = get_dimensions(vf)
        if w is None:
            print(f"  [SKIP] {vf} — impossibile leggere dimensioni")
            skipped += 1
            continue

        if w <= h:
            # Già verticale o quadrato
            if w == TARGET_W and h == TARGET_H:
                continue  # Dimensioni già perfette
            # Verticale ma dimensioni diverse: scala a 1080x1920
            print(f"  [SCALE] {vf} ({w}x{h}) → {TARGET_W}x{TARGET_H}")
            if scale_to_vertical(vf):
                nw, nh = get_dimensions(vf)
                print(f"          Risultato: {nw}x{nh}, dimensione: {vf.stat().st_size / 1024 / 1024:.1f} MB")
                converted += 1
            else:
                errors += 1
            continue

        print(f"  [CONV] {vf} ({w}x{h}) → {TARGET_W}x{TARGET_H}")
        if convert_to_vertical(vf):
            # Verifica il risultato
            nw, nh = get_dimensions(vf)
            print(f"         Risultato: {nw}x{nh}, dimensione: {vf.stat().st_size / 1024 / 1024:.1f} MB")
            converted += 1
        else:
            errors += 1

    print(f"\nRiepilogo: {converted} convertiti, {skipped} saltati, {errors} errori")


if __name__ == "__main__":
    main()
