#!/usr/bin/env python3
"""Scarica i file media mancanti da Google Drive.

Per ogni post in posts.json che ha un driveId ma il cui file media
non esiste nel repo, scarica il file da Google Drive.

PREREQUISITI:
  1. Un Service Account Google con accesso alla cartella Drive
  2. Il file delle credenziali JSON del Service Account
  3. La variabile d'ambiente GOOGLE_SERVICE_ACCOUNT_JSON con il contenuto JSON
     OPPURE il file credentials.json nella root del progetto

UTILIZZO LOCALE:
  export GOOGLE_SERVICE_ACCOUNT_JSON='{"type":"service_account",...}'
  python3 download_missing_media.py

UTILIZZO IN GITHUB ACTIONS:
  Il secret GOOGLE_SERVICE_ACCOUNT_JSON viene impostato come variabile d'ambiente.
"""
import json
import os
import sys
from pathlib import Path

POSTS_FILE = Path("posts.json")
MEDIA_DIR = Path("media")


def get_drive_service():
    """Crea un client Google Drive autenticato."""
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
    except ImportError:
        print("Installo google-api-python-client...")
        import subprocess
        subprocess.check_call([
            sys.executable, "-m", "pip", "install",
            "google-api-python-client", "google-auth",
            "--quiet", "--break-system-packages"
        ])
        from google.oauth2 import service_account
        from googleapiclient.discovery import build

    # Credenziali da variabile d'ambiente o file
    creds_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
    if creds_json:
        import io
        creds_data = json.loads(creds_json)
        credentials = service_account.Credentials.from_service_account_info(
            creds_data,
            scopes=["https://www.googleapis.com/auth/drive.readonly"]
        )
    elif Path("credentials.json").exists():
        credentials = service_account.Credentials.from_service_account_file(
            "credentials.json",
            scopes=["https://www.googleapis.com/auth/drive.readonly"]
        )
    else:
        print("ERRORE: Nessuna credenziale Google trovata.")
        print("Imposta GOOGLE_SERVICE_ACCOUNT_JSON o crea credentials.json")
        return None

    return build("drive", "v3", credentials=credentials)


def download_file(service, file_id, dest_path):
    """Scarica un file da Google Drive."""
    from googleapiclient.http import MediaIoBaseDownload
    import io

    request = service.files().get_media(fileId=file_id)
    dest_path.parent.mkdir(parents=True, exist_ok=True)

    with open(dest_path, "wb") as f:
        downloader = MediaIoBaseDownload(f, request)
        done = False
        while not done:
            status, done = downloader.next_chunk()
            if status:
                pct = int(status.progress() * 100)
                print(f"  Download: {pct}%", end="\r")

    size_mb = dest_path.stat().st_size / 1024 / 1024
    print(f"  Scaricato: {dest_path} ({size_mb:.1f} MB)")
    return True


def main():
    if not POSTS_FILE.exists():
        print("posts.json non trovato.")
        return

    posts = json.loads(POSTS_FILE.read_text(encoding="utf-8"))

    # Trova post con driveId e file mancante
    missing = []
    for post in posts:
        drive_id = post.get("driveId", "")
        media = post.get("media", "")
        if drive_id and media and not media.startswith("http"):
            media_path = Path(media)
            if not media_path.exists():
                missing.append({
                    "id": post.get("id", ""),
                    "media": media,
                    "driveId": drive_id,
                    "type": post.get("type", ""),
                    "when": post.get("when", ""),
                })

    if not missing:
        print("Tutti i file media sono presenti. Nulla da scaricare.")
        return

    print(f"File media mancanti: {len(missing)}")
    for m in missing:
        print(f"  {m['id']} → {m['media']} (Drive: {m['driveId'][:12]}...)")

    # Inizializza Google Drive
    service = get_drive_service()
    if not service:
        # Senza credenziali, elenca solo i file mancanti
        print("\nSenza credenziali Google non posso scaricare.")
        print("File mancanti che richiedono download manuale:")
        for m in missing:
            url = f"https://drive.google.com/file/d/{m['driveId']}/view"
            print(f"  {m['media']} → {url}")
        sys.exit(1)

    # Scarica tutti i file mancanti
    downloaded = 0
    errors = 0
    for m in missing:
        print(f"\nScaricando: {m['id']}...")
        try:
            dest = Path(m["media"])
            if download_file(service, m["driveId"], dest):
                downloaded += 1
        except Exception as e:
            print(f"  ERRORE: {e}")
            errors += 1

    print(f"\nRiepilogo: {downloaded} scaricati, {errors} errori, "
          f"{len(missing) - downloaded - errors} saltati")

    if errors > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
