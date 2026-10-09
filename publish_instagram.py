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
  MAX_RETRIES         opzionale, default 5: dopo N errori consecutivi il post viene messo in pausa

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
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Rome")
BASE = "https://graph.instagram.com"
POSTS_FILE = Path(os.environ.get("POSTS_FILE", "posts.json"))
STATE_FILE = Path(os.environ.get("STATE_FILE", "published.json"))
MAX_LATE = timedelta(hours=float(os.environ.get("MAX_LATE_HOURS", "12")))
MAX_RETRIES = int(os.environ.get("MAX_RETRIES", "5"))


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
    except URLError as e:
        raise RuntimeError(f"Errore di rete su /{path}: {e.reason}") from None


def check_token(token, uid):
    """Verifica che il token sia valido prima di tentare qualsiasi pubblicazione."""
    try:
        result = api("GET", uid, {"fields": "id,username", "access_token": token})
        print(f"[TOKEN OK] Account: {result.get('username', result.get('id'))}")
        return True
    except RuntimeError as e:
        print(f"[TOKEN ERRORE] {e}", file=sys.stderr)
        return False


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

    # Verifica token prima di tentare qualsiasi pubblicazione
    has_pending = False
    for post in posts:
        pid = post["id"]
        if pid in state and state[pid].get("status") != "errore":
            continue
        if "instagram" not in post.get("platforms", ["instagram", "facebook"]):
            continue
        when = datetime.fromisoformat(post["when"]).replace(tzinfo=TZ)
        if when <= now and now - when <= MAX_LATE:
            has_pending = True
            break

    if has_pending and not args.dry_run:
        if not check_token(token, uid):
            # Token non valido: salva l'errore e esci subito
            # (evita di riprovare ogni 15 min con un token scaduto)
            for post in posts:
                pid = post["id"]
                if pid in state and state[pid].get("status") != "errore":
                    continue
                if "instagram" not in post.get("platforms", ["instagram", "facebook"]):
                    continue
                when = datetime.fromisoformat(post["when"]).replace(tzinfo=TZ)
                if when <= now and now - when <= MAX_LATE:
                    retry_count = state.get(pid, {}).get("tentativi", 0) + 1
                    state[pid] = {
                        "status": "errore",
                        "errore": "Token Instagram non valido o scaduto",
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
            # Per post in stato "errore", riprova
            if st == "errore" and state[pid].get("tentativi", 0) >= MAX_RETRIES:
                state[pid]["status"] = "errore_bloccato"
                state[pid]["motivo"] = f"Bloccato dopo {state[pid]['tentativi']} tentativi falliti"
                print(f"[{pid}] BLOCCATO dopo {state[pid]['tentativi']} tentativi")
                STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
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
            retry_count = state.get(pid, {}).get("tentativi", 0)
            try:
                media_id = publish(post, token, uid)
                state[pid] = {"status": "pubblicato", "media_id": media_id, "alle": now.isoformat()}
                print(f"[{pid}] PUBBLICATO (media {media_id})")
            except Exception as e:
                errors += 1
                retry_count += 1
                error_msg = str(e)
                # Salva errore nel state per diagnostica
                state[pid] = {
                    "status": "errore",
                    "errore": error_msg[:500],  # tronca messaggi troppo lunghi
                    "tentativi": retry_count,
                    "ultimo_tentativo": now.isoformat(),
                }
                if retry_count >= MAX_RETRIES:
                    state[pid]["status"] = "errore_bloccato"
                    state[pid]["motivo"] = f"Bloccato dopo {retry_count} tentativi: {error_msg[:200]}"
                    print(f"[{pid}] BLOCCATO dopo {retry_count} tentativi: {error_msg[:200]}", file=sys.stderr)
                else:
                    print(f"[{pid}] ERRORE (tentativo {retry_count}/{MAX_RETRIES}): {error_msg[:200]}", file=sys.stderr)
                # Salva subito lo stato errore
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
