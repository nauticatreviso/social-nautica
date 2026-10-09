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
  MAX_RETRIES         opzionale, default 5: dopo N errori consecutivi il post viene messo in pausa

Uso locale per prova (non chiama Facebook):
  python publish_facebook.py --dry-run
"""
import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Rome")
VERSION = os.environ.get("FB_API_VERSION", "v26.0")
POSTS_FILE = Path(os.environ.get("POSTS_FILE", "posts.json"))
STATE_FILE = Path(os.environ.get("STATE_FILE", "published_facebook.json"))
MAX_LATE = timedelta(hours=float(os.environ.get("MAX_LATE_HOURS", "12")))
MAX_RETRIES = int(os.environ.get("MAX_RETRIES", "5"))


def api_post(host, path, params):
    """Chiamata POST all'API. Non stampa mai l'URL ne' i parametri (c'e' il token)."""
    url = f"https://{host}/{VERSION}/{path}"
    try:
        with urlopen(Request(url, data=urlencode(params).encode(), method="POST"), timeout=120) as r:
            return json.load(r)
    except HTTPError as e:
        body = e.read().decode(errors="replace")
        raise RuntimeError(f"HTTP {e.code} su /{path}: {body}") from None
    except URLError as e:
        raise RuntimeError(f"Errore di rete su /{path}: {e.reason}") from None


def check_token(token, page_id):
    """Verifica che il token sia valido prima di tentare qualsiasi pubblicazione."""
    try:
        url = f"https://graph.facebook.com/{VERSION}/{page_id}"
        params = urlencode({"fields": "id,name", "access_token": token})
        with urlopen(Request(f"{url}?{params}"), timeout=30) as r:
            result = json.load(r)
        print(f"[TOKEN OK] Pagina: {result.get('name', result.get('id'))}")
        return True
    except Exception as e:
        print(f"[TOKEN ERRORE] {e}", file=sys.stderr)
        return False


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

    # Verifica token prima di tentare qualsiasi pubblicazione
    has_pending = False
    for post in posts:
        pid = post["id"]
        if pid in state and state[pid].get("status") != "errore":
            continue
        if "facebook" not in post.get("platforms", ["instagram", "facebook"]):
            continue
        when = datetime.fromisoformat(post["when"]).replace(tzinfo=TZ)
        if when <= now and now - when <= MAX_LATE:
            has_pending = True
            break

    if has_pending and not args.dry_run:
        if not check_token(token, page_id):
            for post in posts:
                pid = post["id"]
                if pid in state and state[pid].get("status") != "errore":
                    continue
                if "facebook" not in post.get("platforms", ["instagram", "facebook"]):
                    continue
                when = datetime.fromisoformat(post["when"]).replace(tzinfo=TZ)
                if when <= now and now - when <= MAX_LATE:
                    retry_count = state.get(pid, {}).get("tentativi", 0) + 1
                    state[pid] = {
                        "status": "errore",
                        "errore": "Token Facebook non valido o scaduto",
                        "tentativi": retry_count,
                        "ultimo_tentativo": now.isoformat(),
                    }
                    if retry_count >= MAX_RETRIES:
                        state[pid]["status"] = "errore_bloccato"
                        state[pid]["motivo"] = f"Bloccato dopo {retry_count} tentativi falliti"
                        print(f"[{pid}] BLOCCATO: token non valido dopo {retry_count} tentativi")
                    else:
                        print(f"[{pid}] ERRORE TOKEN: tentativo {retry_count}/{MAX_RETRIES}")
            STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
            sys.exit(1)

    for post in posts:
        pid = post["id"]
        # Salta post gia' pubblicati o bloccati definitivamente
        if pid in state:
            st = state[pid].get("status")
            if st in ("pubblicato", "saltato", "errore_bloccato"):
                continue
            if st == "errore" and state[pid].get("tentativi", 0) >= MAX_RETRIES:
                state[pid]["status"] = "errore_bloccato"
                state[pid]["motivo"] = f"Bloccato dopo {state[pid]['tentativi']} tentativi falliti"
                print(f"[{pid}] BLOCCATO dopo {state[pid]['tentativi']} tentativi")
                STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
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
            retry_count = state.get(pid, {}).get("tentativi", 0)
            try:
                fb_id = publish(post, token, page_id)
                state[pid] = {"status": "pubblicato", "post_id": fb_id, "alle": now.isoformat()}
                print(f"[{pid}] PUBBLICATO su Facebook (id {fb_id})")
            except Exception as e:
                errors += 1
                retry_count += 1
                error_msg = str(e)
                state[pid] = {
                    "status": "errore",
                    "errore": error_msg[:500],
                    "tentativi": retry_count,
                    "ultimo_tentativo": now.isoformat(),
                }
                if retry_count >= MAX_RETRIES:
                    state[pid]["status"] = "errore_bloccato"
                    state[pid]["motivo"] = f"Bloccato dopo {retry_count} tentativi: {error_msg[:200]}"
                    print(f"[{pid}] BLOCCATO dopo {retry_count} tentativi: {error_msg[:200]}", file=sys.stderr)
                else:
                    print(f"[{pid}] ERRORE (tentativo {retry_count}/{MAX_RETRIES}): {error_msg[:200]}", file=sys.stderr)
                STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
                continue
        # salva subito: evita doppioni se il processo si interrompe
        if not args.dry_run:
            STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")

    if not args.dry_run:
        STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
