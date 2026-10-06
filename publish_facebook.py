#!/usr/bin/env python3
"""Pubblica sulla Pagina Facebook i post programmati in posts.json.

Pensato per girare ogni 15 minuti su GitHub Actions, insieme a publish_instagram.py.
Usa solo la libreria standard di Python (nessuna installazione).

Ogni post di posts.json puo' avere il campo opzionale "platforms":
  "platforms": ["instagram", "facebook"]   (o solo uno dei due)
Se manca, il post va su entrambi.

Variabili d'ambiente:
  FB_PAGE_TOKEN       token della Pagina (Secret di GitHub)
  FB_PAGE_ID          ID della Pagina (es. 1479050282386421)
  FB_API_VERSION      opzionale, default v26.0
  GITHUB_REPOSITORY   impostata da GitHub (es. utente/repo)
  GITHUB_REF_NAME     impostata da GitHub (es. main)
  POSTS_FILE          opzionale, default posts.json
  STATE_FILE          opzionale, default published_facebook.json
  MAX_LATE_HOURS      opzionale, default 12: oltre questo ritardo il post viene saltato

Uso locale per prova (non chiama Facebook):
  python publish_facebook.py --dry-run
"""
import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Rome")
VERSION = os.environ.get("FB_API_VERSION", "v26.0")
POSTS_FILE = Path(os.environ.get("POSTS_FILE", "posts.json"))
STATE_FILE = Path(os.environ.get("STATE_FILE", "published_facebook.json"))
MAX_LATE = timedelta(hours=float(os.environ.get("MAX_LATE_HOURS", "12")))


def api_post(host, path, params):
    """Chiamata POST all'API. Non stampa mai l'URL ne' i parametri (c'e' il token)."""
    url = f"https://{host}/{VERSION}/{path}"
    try:
        with urlopen(Request(url, data=urlencode(params).encode(), method="POST"), timeout=120) as r:
            return json.load(r)
    except HTTPError as e:
        body = e.read().decode(errors="replace")
        raise RuntimeError(f"HTTP {e.code} su /{path}: {body}") from None


def media_url(media):
    """URL pubblico del file: link diretto oppure file del repository GitHub."""
    if media.startswith("http"):
        return media
    repo = os.environ.get("GITHUB_REPOSITORY", "UTENTE/REPO")
    branch = os.environ.get("GITHUB_REF_NAME", "main")
    return f"https://raw.githubusercontent.com/{repo}/{branch}/{quote(media)}"


def publish(post, token, page_id):
    url = media_url(post["media"])
    if post.get("type", "image") == "video":
        res = api_post("graph-video.facebook.com", f"{page_id}/videos",
                       {"file_url": url, "description": post["caption"], "access_token": token})
    else:
        res = api_post("graph.facebook.com", f"{page_id}/photos",
                       {"url": url, "message": post["caption"], "published": "true", "access_token": token})
    return res.get("post_id") or res["id"]


def load(path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="mostra cosa farebbe, senza pubblicare")
    args = ap.parse_args()

    token = os.environ.get("FB_PAGE_TOKEN")
    page_id = os.environ.get("FB_PAGE_ID")
    if not args.dry_run and not (token and page_id):
        sys.exit("Mancano FB_PAGE_TOKEN e/o FB_PAGE_ID")

    posts = load(POSTS_FILE, [])
    state = load(STATE_FILE, {})
    now = datetime.now(TZ)
    errors = 0

    for post in posts:
        pid = post["id"]
        if pid in state:
            continue
        if "facebook" not in post.get("platforms", ["instagram", "facebook"]):
            continue
        when = datetime.fromisoformat(post["when"]).replace(tzinfo=TZ)
        if when > now:
            continue
        if now - when > MAX_LATE:
            state[pid] = {"status": "saltato", "motivo": "troppo in ritardo", "controllato": now.isoformat()}
            print(f"[{pid}] SALTATO: previsto {post['when']}, oltre {MAX_LATE} di ritardo")
        elif args.dry_run:
            print(f"[{pid}] DA PUBBLICARE su Facebook ({post.get('type', 'image')}): {media_url(post['media'])}")
            continue
        else:
            try:
                fb_id = publish(post, token, page_id)
                state[pid] = {"status": "pubblicato", "post_id": fb_id, "alle": now.isoformat()}
                print(f"[{pid}] PUBBLICATO su Facebook (id {fb_id})")
            except Exception as e:  # riprova al giro successivo
                errors += 1
                print(f"[{pid}] ERRORE: {e}", file=sys.stderr)
                continue
        # salva subito: evita doppioni se il processo si interrompe
        if not args.dry_run:
            STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")

    if not args.dry_run:
        STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
