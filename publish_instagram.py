#!/usr/bin/env python3
"""Pubblica su Instagram i post programmati in posts.json.

Pensato per girare ogni 15 minuti su GitHub Actions.
Usa solo la libreria standard di Python (nessuna installazione).

Variabili d'ambiente:
  IG_TOKEN            token Instagram (Secret di GitHub)
  IG_USER_ID          ID account Instagram (es. 17841404468831152)
  GITHUB_REPOSITORY   impostata da GitHub (es. utente/repo)
  GITHUB_REF_NAME     impostata da GitHub (es. main)
  POSTS_FILE          opzionale, default posts.json
  STATE_FILE          opzionale, default published.json
  MAX_LATE_HOURS      opzionale, default 12: oltre questo ritardo il post viene saltato

Uso locale per prova (non chiama Instagram):
  python publish_instagram.py --dry-run
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Rome")
BASE = "https://graph.instagram.com"
POSTS_FILE = Path(os.environ.get("POSTS_FILE", "posts.json"))
STATE_FILE = Path(os.environ.get("STATE_FILE", "published.json"))
MAX_LATE = timedelta(hours=float(os.environ.get("MAX_LATE_HOURS", "12")))


def api(method, path, params):
    """Chiamata all'API. Non stampa mai l'URL (contiene il token)."""
    url = f"{BASE}/{path}"
    data = None
    if method == "GET":
        url += "?" + urlencode(params)
    else:
        data = urlencode(params).encode()
    try:
        with urlopen(Request(url, data=data, method=method), timeout=60) as r:
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


def publish(post, token, uid):
    params = {"caption": post["caption"], "access_token": token}
    url = media_url(post["media"])
    if post.get("type", "image") == "video":
        params.update(media_type="REELS", video_url=url)
    else:
        params["image_url"] = url

    container = api("POST", f"{uid}/media", params)["id"]

    # attende che Instagram elabori il file (i video richiedono piu' tempo)
    for _ in range(40):
        status = api("GET", container, {"fields": "status_code", "access_token": token})
        code = status.get("status_code")
        if code == "FINISHED":
            break
        if code in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"Elaborazione fallita: {status}")
        time.sleep(10)
    else:
        raise RuntimeError("Timeout: Instagram non ha finito di elaborare il file")

    return api("POST", f"{uid}/media_publish", {"creation_id": container, "access_token": token})["id"]


def load(path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="mostra cosa farebbe, senza pubblicare")
    args = ap.parse_args()

    token = os.environ.get("IG_TOKEN")
    uid = os.environ.get("IG_USER_ID")
    if not args.dry_run and not (token and uid):
        sys.exit("Mancano IG_TOKEN e/o IG_USER_ID")

    posts = load(POSTS_FILE, [])
    state = load(STATE_FILE, {})
    now = datetime.now(TZ)
    errors = 0

    for post in posts:
        pid = post["id"]
        if pid in state:
            continue
        if "instagram" not in post.get("platforms", ["instagram", "facebook"]):
            continue
        when = datetime.fromisoformat(post["when"]).replace(tzinfo=TZ)
        if when > now:
            continue
        if now - when > MAX_LATE:
            state[pid] = {"status": "saltato", "motivo": "troppo in ritardo", "controllato": now.isoformat()}
            print(f"[{pid}] SALTATO: previsto {post['when']}, oltre {MAX_LATE} di ritardo")
        elif args.dry_run:
            print(f"[{pid}] DA PUBBLICARE ({post.get('type', 'image')}): {media_url(post['media'])}")
            continue
        else:
            try:
                media_id = publish(post, token, uid)
                state[pid] = {"status": "pubblicato", "media_id": media_id, "alle": now.isoformat()}
                print(f"[{pid}] PUBBLICATO (media {media_id})")
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
