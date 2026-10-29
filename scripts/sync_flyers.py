#!/usr/bin/env python3
"""
Bandsintownの新規イベントを検出し、チケットサイトのOG画像をフライヤーとして取得する。
flyers/YYYY-MM-DD.jpg として保存し、schedule.html が自動で表示する。
"""
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

from PIL import Image

BIT_APP_ID     = os.environ.get("BIT_APP_ID", "59eb73f72056941927ca5a752b0891de")
SERPER_API_KEY = os.environ.get("SERPER_API_KEY", "d08fd64cb666b07e7ce8dc602676cec1f393f9c9")
BIT_ARTIST     = "MoritaSaki in the pool"
FLYER_DIR      = Path(__file__).resolve().parent.parent / "flyers"
CACHE_FILE     = Path(__file__).resolve().parent / "event_cache.json"

MAX_WIDTH    = 1200
JPEG_QUALITY = 82

HEADERS = {
    "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ja,en;q=0.9",
}


# ── ユーティリティ ────────────────────────────────────────────

def fetch(url, timeout=20, extra_headers=None):
    headers = {**HEADERS, **(extra_headers or {})}
    req = urllib.request.Request(url, headers=headers) if isinstance(url, str) else url
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read(), resp.headers.get("Content-Type", "").split(";")[0].strip()


def resolve_redirect(url, timeout=15):
    """リダイレクトを追跡して最終URLを返す"""
    headers = {**HEADERS}
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.url


def optimize_image(data):
    with Image.open(io.BytesIO(data)) as im:
        im = im.convert("RGB")
        if im.width > MAX_WIDTH:
            h = round(im.height * MAX_WIDTH / im.width)
            im = im.resize((MAX_WIDTH, h), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=JPEG_QUALITY, optimize=True)
        return buf.getvalue()


# ── Bandsintown ───────────────────────────────────────────────

def fetch_events():
    import urllib.parse
    url = (f"https://rest.bandsintown.com/artists/"
           f"{urllib.parse.quote(BIT_ARTIST)}/events"
           f"?app_id={BIT_APP_ID}")
    data, _ = fetch(url)
    return json.loads(data)


def parse_event_date(event):
    s = event.get("starts_at") or event.get("datetime", "")
    if not s:
        return None
    try:
        return datetime.fromisoformat(s).date()
    except ValueError:
        return None


# ── キャッシュ ────────────────────────────────────────────────

def load_cache():
    if CACHE_FILE.exists():
        return set(json.loads(CACHE_FILE.read_text()))
    return set()


def save_cache(ids):
    CACHE_FILE.write_text(json.dumps(sorted(ids)))


# ── チケットページからOG画像取得 ──────────────────────────────

def serper_search(query, search_type="search"):
    endpoint = f"https://google.serper.dev/{search_type}"
    payload = json.dumps({"q": query, "num": 5, "gl": "jp", "hl": "ja"}).encode()
    req = urllib.request.Request(
        endpoint, data=payload,
        headers={
            "X-API-KEY": SERPER_API_KEY,
            "Content-Type": "application/json",
        }
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


def get_og_image(page_url):
    try:
        data, _ = fetch(page_url)
        html = data.decode("utf-8", errors="ignore")
        m = re.search(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\'](https?://[^"\']+)', html)
        if not m:
            m = re.search(r'content=["\'](https?://[^"\']+)[^>]+property=["\']og:image["\']', html)
        return m.group(1) if m else None
    except Exception:
        return None


def find_flyer(event_title, vendor_name, ticket_url=None):
    """チケットサイトのページを探してOG画像を返す"""
    # まずBandsintownのチケットURLからリダイレクト先を直接試みる
    if ticket_url:
        try:
            final_url = resolve_redirect(ticket_url)
            print(f"  チケット直リンク: {final_url[:70]}")
            og = get_og_image(final_url)
            if og and len(og) > 50:
                return og
        except Exception as e:
            print(f"  [redirect error] {e}")

    # 特殊文字を除去してクリーンな検索クエリを作成
    clean_title = re.sub(r"[『』「」【】《》×・。、！？!?♪〜~◆◇■□●○▶▷★☆※]", " ", event_title)
    clean_title = re.sub(r"\s+", " ", clean_title).strip()
    short_title = clean_title[:50]

    # チケットサイトを特定してサイト指定検索
    vendor_domains = []
    if vendor_name:
        v = vendor_name.lower()
        if "livepocket" in v:   vendor_domains = ["livepocket.jp"]
        elif "tiget" in v:      vendor_domains = ["tiget.net"]
        elif "zaiko" in v:      vendor_domains = ["zaiko.io"]
        elif "eplus" in v:      vendor_domains = ["eplus.jp"]
        elif "tixplus" in v:    vendor_domains = ["tixplus.jp"]
        elif "peatix" in v:     vendor_domains = ["peatix.com"]
        else:                   vendor_domains = [vendor_name]

    queries = []
    for domain in vendor_domains:
        queries.append(f"{short_title} {domain}")       # "title livepocket.jp"
        queries.append(f"{short_title} site:{domain}")  # site: 指定も試す
    queries.append(f"{short_title} {BIT_ARTIST} チケット")

    # イベントページのURLパターン（サイト汎用ページは除外）
    event_url_patterns = [
        r"livepocket\.jp/e/[^/]+$",
        r"tiget\.net/events/\d+",
        r"zaiko\.io/event/\d+",
        r"eplus\.jp/sf/detail/\w+",
        r"tixplus\.jp/[^/]+/detail",
        r"peatix\.com/event/\d+",
    ]
    skip_patterns = [
        "search", "users/", "performers/", "webclip", "tag/", "word/",
        "ticketjam.jp", "bandsintown.com",
    ]
    MIN_FLYER_BYTES = 30_000  # ロゴや小アイコンを除外

    for query in queries:
        try:
            results = serper_search(query)
            links = [item.get("link", "") for item in results.get("organic", [])]

            # イベントURLパターンに合うものを優先
            for link in links:
                if any(s in link for s in skip_patterns):
                    continue
                if any(re.search(p, link) for p in event_url_patterns):
                    og = get_og_image(link)
                    if og and len(og) > 50:
                        print(f"  ページ: {link[:60]}")
                        return og

            # パターン外でもスキップ対象でなければ試す（サイズチェック付き）
            for link in links:
                if any(s in link for s in skip_patterns):
                    continue
                og = get_og_image(link)
                if og and len(og) > 50:
                    # 画像を実際に取得してサイズ確認
                    try:
                        img_data, _ = fetch(og)
                        if len(img_data) >= MIN_FLYER_BYTES:
                            print(f"  ページ: {link[:60]}")
                            return og
                        else:
                            print(f"  [skip] 画像が小さすぎ({len(img_data):,}B): {link[:50]}")
                    except Exception:
                        pass

            time.sleep(0.5)
        except Exception as e:
            print(f"  [search error] {e}")
            continue

    return None


# ── メイン ────────────────────────────────────────────────────

def main():
    import urllib.parse

    print("📡 Bandsintown からイベントを取得中...")
    try:
        events = fetch_events()
    except Exception as e:
        print(f"[fail] Bandsintown API エラー: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"  {len(events)} 件のイベントを取得")

    cached_ids = load_cache()
    FLYER_DIR.mkdir(parents=True, exist_ok=True)

    new_ids = set()
    ok = skip = fail = 0

    for event in events:
        event_id   = event.get("id", "")
        title      = event.get("title", "").strip()
        d          = parse_event_date(event)
        vendor     = (event.get("offers") or [{}])[0].get("vendor_name", "")
        ticket_url = (event.get("offers") or [{}])[0].get("url", "") or None

        if not d or not title:
            continue

        new_ids.add(event_id)
        out_path = FLYER_DIR / f"{d.isoformat()}.jpg"

        if out_path.exists() and event_id in cached_ids:
            skip += 1
            continue

        print(f"\n🔍 [{d}] {title[:50]}")
        print(f"  チケットサイト: {vendor or '不明'}")

        og_url = find_flyer(title, vendor, ticket_url)
        if not og_url:
            print(f"  [skip] フライヤーが見つかりませんでした")
            fail += 1
            continue

        print(f"  画像: {og_url[:70]}...")
        try:
            img_data, ctype = fetch(og_url)
            is_image = (ctype.startswith("image/") or
                        any(ext in og_url.lower() for ext in [".jpg", ".jpeg", ".png", ".webp"]) or
                        len(img_data) > 5000)
            if not is_image:
                raise ValueError(f"非画像レスポンス: {ctype}")
            data = optimize_image(img_data)
        except Exception as e:
            print(f"  [fail] 画像取得エラー: {e}")
            fail += 1
            continue

        out_path.write_bytes(data)
        print(f"  [ok] {out_path.name} ({len(data):,} bytes)")
        ok += 1

    save_cache(cached_ids | new_ids)
    print(f"\n✅ 完了: {ok} 取得, {skip} スキップ, {fail} 失敗")


if __name__ == "__main__":
    main()
