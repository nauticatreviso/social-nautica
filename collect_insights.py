#!/usr/bin/env python3
"""Raccoglie le metriche (like, commenti, reach) dai post pubblicati
su Instagram e Facebook e salva tutto in insights.json.

Pensato per girare su GitHub Actions dopo la pubblicazione.
Usa solo la libreria standard di Python.

Variabili d'ambiente:
  IG_TOKEN        token Instagram (Secret di GitHub)
  IG_USER_ID      ID account Instagram
  FB_PAGE_TOKEN   token pagina Facebook (Secret di GitHub)
  FB_PAGE_ID      ID pagina Facebook
"""
import json
import os
import sys
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

IG_STATE = Path("published.json")
FB_STATE = Path("published_facebook.json")
OUTPUT   = Path("insights.json")


def api_get(url, params):
    """GET generico verso Graph API."""
    full = url + "?" + urlencode(params)
    try:
        with urlopen(Request(full, method="GET"), timeout=30) as r:
            return json.load(r)
    except HTTPError as e:
        body = e.read().decode(errors="replace")
        print(f"  ⚠ HTTP {e.code}: {body[:200]}", file=sys.stderr)
        return None


def load(path):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def collect_ig(token):
    """Raccoglie metriche Instagram da ogni media_id in published.json."""
    state = load(IG_STATE)
    results = {}

    for pid, info in state.items():
        if info.get("status") != "pubblicato":
            continue
        media_id = info.get("media_id")
        if not media_id:
            continue

        # Campi base del media
        data = api_get(
            f"https://graph.instagram.com/{media_id}",
            {"fields": "like_count,comments_count,timestamp,media_type", "access_token": token},
        )
        if not data:
            print(f"  [IG] {pid}: impossibile leggere media {media_id}")
            continue

        entry = {
            "likes": data.get("like_count", 0),
            "comments": data.get("comments_count", 0),
            "reach": 0,
            "impressions": 0,
            "timestamp": data.get("timestamp", ""),
            "media_type": data.get("media_type", ""),
        }

        # Insights (reach, impressions) — disponibili solo per media del Business account
        metric = "reach,impressions"
        insights = api_get(
            f"https://graph.instagram.com/{media_id}/insights",
            {"metric": metric, "access_token": token},
        )
        if insights and "data" in insights:
            for m in insights["data"]:
                name = m.get("name")
                val = 0
                # Instagram restituisce values come lista
                if "values" in m and m["values"]:
                    val = m["values"][0].get("value", 0)
                elif "value" in m:
                    val = m["value"]
                if name in ("reach", "impressions"):
                    entry[name] = val

        results[pid] = entry
        print(f"  [IG] {pid}: ❤️ {entry['likes']}  💬 {entry['comments']}  👁 {entry['reach']}")

    return results


def collect_fb(token):
    """Raccoglie metriche Facebook da ogni post_id in published_facebook.json."""
    state = load(FB_STATE)
    results = {}

    for pid, info in state.items():
        if info.get("status") != "pubblicato":
            continue
        post_id = info.get("post_id")
        if not post_id:
            continue

        data = api_get(
            f"https://graph.facebook.com/v21.0/{post_id}",
            {
                "fields": "likes.summary(true),comments.summary(true),shares",
                "access_token": token,
            },
        )
        if not data:
            print(f"  [FB] {pid}: impossibile leggere post {post_id}")
            continue

        likes = data.get("likes", {}).get("summary", {}).get("total_count", 0)
        comments = data.get("comments", {}).get("summary", {}).get("total_count", 0)
        shares = data.get("shares", {}).get("count", 0)

        # Insights del post (reach)
        reach = 0
        insights = api_get(
            f"https://graph.facebook.com/v21.0/{post_id}/insights",
            {"metric": "post_impressions_unique", "access_token": token},
        )
        if insights and "data" in insights:
            for m in insights["data"]:
                if m.get("name") == "post_impressions_unique":
                    vals = m.get("values", [])
                    if vals:
                        reach = vals[0].get("value", 0)

        results[pid] = {
            "likes": likes,
            "comments": comments,
            "shares": shares,
            "reach": reach,
        }
        print(f"  [FB] {pid}: ❤️ {likes}  💬 {comments}  🔄 {shares}  👁 {reach}")

    return results


def main():
    ig_token = os.environ.get("IG_TOKEN", "")
    fb_token = os.environ.get("FB_PAGE_TOKEN", "")

    # Carica dati precedenti se esistono
    existing = load(OUTPUT)
    if isinstance(existing, list):
        # Converte dal vecchio formato array al nuovo dict
        existing = {}

    ig_data = {}
    fb_data = {}

    if ig_token:
        print("📊 Raccolta metriche Instagram...")
        ig_data = collect_ig(ig_token)
    else:
        print("⚠ IG_TOKEN non impostato, salto Instagram")

    if fb_token:
        print("📊 Raccolta metriche Facebook...")
        fb_data = collect_fb(fb_token)
    else:
        print("⚠ FB_PAGE_TOKEN non impostato, salto Facebook")

    # Unisce i dati: per ogni post, somma IG + FB
    all_pids = set(list(ig_data.keys()) + list(fb_data.keys()))
    if not all_pids:
        print("Nessun post pubblicato trovato.")
        return

    # Formato finale: array di oggetti (compatibile con manager.html)
    output = []
    for pid in sorted(all_pids):
        ig = ig_data.get(pid, {})
        fb = fb_data.get(pid, {})
        output.append({
            "post_id": pid,
            "likes": ig.get("likes", 0) + fb.get("likes", 0),
            "comments": ig.get("comments", 0) + fb.get("comments", 0),
            "reach": ig.get("reach", 0) + fb.get("reach", 0),
            "shares_fb": fb.get("shares", 0),
            "impressions_ig": ig.get("impressions", 0),
            "ig": {k: v for k, v in ig.items() if v} if ig else None,
            "fb": {k: v for k, v in fb.items() if v} if fb else None,
        })

    OUTPUT.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n✅ Salvati dati per {len(output)} post in {OUTPUT}")


if __name__ == "__main__":
    main()
