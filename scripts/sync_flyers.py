#!/usr/bin/env python3
"""Googleフォーム(スプレッドシート)の「フライヤー」列にあるDriveリンクから
画像を取得し、band-homepage/flyers/YYYY-MM-DD.<ext> として保存する。

サイト側(schedule.html)はGoogle Driveへ直接アクセスせず、この
スクリプトが事前に取り込んだローカル画像だけを表示する。
"""
import csv
import io
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

from PIL import Image

CSV_URL = (
    "https://docs.google.com/spreadsheets/d/e/2PACX-1vTBU3fniYvhEvFTq2Le3HzDHWzuyrh0nlVZgniNIg-2vDU1CWDxXqXTZvcTR6Nn58ucQ6Ej7nih8Wwj"
    "/pub?gid=2012153675&single=true&output=csv"
)
FLYER_DIR = Path(__file__).resolve().parent.parent / "flyers"
API_KEY = os.environ.get("GDRIVE_API_KEY", "")

DRIVE_ID_RE = re.compile(r"[?&]id=([\w-]+)|/d/([\w-]+)")
DATE_RE = re.compile(r"(\d{4})[./\-年](\d{1,2})[./\-月](\d{1,2})")

MAX_WIDTH = 1200
JPEG_QUALITY = 82
KNOWN_EXTS = {"jpg"}


def optimize_image(data):
    """モバイルでも軽く読み込めるよう、幅1200px・JPEG品質82に正規化する。"""
    with Image.open(io.BytesIO(data)) as im:
        im = im.convert("RGB")
        if im.width > MAX_WIDTH:
            h = round(im.height * MAX_WIDTH / im.width)
            im = im.resize((MAX_WIDTH, h), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=JPEG_QUALITY, optimize=True)
        return buf.getvalue()


def drive_file_id(url):
    if not url:
        return None
    m = DRIVE_ID_RE.search(url)
    if not m:
        return None
    return m.group(1) or m.group(2)


def parse_date(s):
    if not s:
        return None
    m = DATE_RE.search(s)
    if not m:
        return None
    y, mo, d = (int(x) for x in m.groups())
    try:
        return date(y, mo, d)
    except ValueError:
        return None


def fetch(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read(), resp.headers.get("Content-Type", "").split(";")[0].strip()


def download_drive_image(file_id):
    # 公式のDrive APIを使う（消費者向けダウンロードリンクはボット判定で
    # ブロックされやすく信頼できないため）。ファイルが「リンクを知っている
    # 全員」に共有されていれば、APIキーだけで匿名取得できる。
    url = f"https://www.googleapis.com/drive/v3/files/{file_id}?alt=media&key={API_KEY}"
    try:
        data, content_type = fetch(url)
    except urllib.error.HTTPError as e:
        return None, None
    if not content_type.startswith("image/"):
        return None, None
    return data, content_type


def main():
    if not API_KEY:
        print("GDRIVE_API_KEY が設定されていません。", file=sys.stderr)
        sys.exit(1)

    text, _ = fetch(CSV_URL)
    reader = csv.DictReader(io.StringIO(text.decode("utf-8")))
    FLYER_DIR.mkdir(parents=True, exist_ok=True)

    seen_bases = set()
    ok = fail = skipped = 0

    for row in reader:
        flyer_url = (row.get("フライヤー") or "").strip()
        date_str = (row.get("日付") or "").strip()
        if not flyer_url or not date_str:
            continue

        file_id = drive_file_id(flyer_url)
        d = parse_date(date_str)
        if not file_id or not d:
            continue

        base = d.isoformat()
        seen_bases.add(base)

        data, content_type = download_drive_image(file_id)
        if not data:
            print(f"[fail] {base}: could not fetch flyer (id={file_id})")
            fail += 1
            continue

        try:
            data = optimize_image(data)
        except Exception as exc:
            print(f"[fail] {base}: could not process image ({exc})")
            fail += 1
            continue

        out_path = FLYER_DIR / f"{base}.jpg"
        if out_path.exists() and out_path.read_bytes() == data:
            skipped += 1
            continue
        out_path.write_bytes(data)
        print(f"[ok] {out_path.name} ({len(data)} bytes)")
        ok += 1

    # スプレッドシート側から消えた日付のフライヤーは削除（過去に取り込んだ古いファイル対策）
    removed = 0
    for f in FLYER_DIR.glob("*.*"):
        if f.suffix.lstrip(".") in KNOWN_EXTS and f.stem not in seen_bases:
            f.unlink()
            removed += 1
            print(f"[remove] {f.name}")

    print(f"done: {ok} updated, {skipped} unchanged, {fail} failed, {removed} removed")


if __name__ == "__main__":
    main()
