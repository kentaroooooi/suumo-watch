"""SUUMO 新着監視スクリプト
- SUUMO_URL の検索結果(PC版・新着順)を取得
- state.json に保存した既知物件と比較し、新着があれば LINE Messaging API で通知
"""
import json
import os
import sys
import time

import requests
from bs4 import BeautifulSoup

URL = os.environ["SUUMO_URL"]
LINE_TOKEN = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
LINE_USER_ID = os.environ["LINE_USER_ID"]
STATE_FILE = "state.json"
MAX_NOTIFY = 10  # 1回の通知で載せる最大件数

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "ja,en;q=0.8",
}


def fetch(url: str) -> str:
    for i in range(3):
        r = requests.get(url, headers=HEADERS, timeout=30)
        if r.status_code == 200:
            return r.text
        time.sleep(5 * (i + 1))
    r.raise_for_status()
    return ""


def parse(html: str) -> dict:
    """物件一覧をパースして {部屋ID: 情報} を返す"""
    soup = BeautifulSoup(html, "html.parser")
    rooms = {}
    for item in soup.select("div.cassetteitem"):
        name = item.select_one(".cassetteitem_content-title")
        name = name.get_text(strip=True) if name else "(物件名不明)"
        addr = item.select_one(".cassetteitem_detail-col1")
        addr = addr.get_text(" ", strip=True) if addr else ""
        for tr in item.select("tr.js-cassette_link"):
            a = tr.select_one("a[href*='/chintai/jnc_']")
            if not a:
                continue
            href = a["href"]
            room_id = href.split("/chintai/")[1].split("/")[0]
            rent = tr.select_one(".cassetteitem_price--rent")
            layout = tr.select_one(".cassetteitem_madori")
            area = tr.select_one(".cassetteitem_menseki")
            rooms[room_id] = {
                "name": name,
                "addr": addr,
                "rent": rent.get_text(strip=True) if rent else "",
                "layout": layout.get_text(strip=True) if layout else "",
                "area": area.get_text(strip=True) if area else "",
                "url": "https://suumo.jp" + href if href.startswith("/") else href,
            }
    return rooms


def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_state(state: dict) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)


def notify_line(text: str) -> None:
    r = requests.post(
        "https://api.line.me/v2/bot/message/push",
        headers={
            "Authorization": f"Bearer {LINE_TOKEN}",
            "Content-Type": "application/json",
        },
        json={"to": LINE_USER_ID, "messages": [{"type": "text", "text": text[:4900]}]},
        timeout=30,
    )
    if r.status_code != 200:
        print("LINE error:", r.status_code, r.text, file=sys.stderr)
        r.raise_for_status()


def main() -> None:
    html = fetch(URL)
    current = parse(html)
    if not current:
        print("WARNING: 物件を1件もパースできませんでした。HTML構造が変わった可能性があります。")
        notify_line("⚠️ SUUMO監視: 物件を取得できませんでした。URLかHTML構造を確認してください。")
        return

    known = load_state()
    first_run = not known
    new_ids = [rid for rid in current if rid not in known]

    if first_run:
        notify_line(f"✅ SUUMO監視を開始しました。現在 {len(current)} 件を記録。以降は新着のみ通知します。")
    elif new_ids:
        lines = [f"🆕 SUUMO新着 {len(new_ids)}件"]
        for rid in new_ids[:MAX_NOTIFY]:
            r = current[rid]
            lines.append(f"\n{r['name']}\n{r['rent']} / {r['layout']} / {r['area']}\n{r['url']}")
        if len(new_ids) > MAX_NOTIFY:
            lines.append(f"\n…他 {len(new_ids) - MAX_NOTIFY} 件")
        notify_line("\n".join(lines))
    else:
        print("新着なし")

    # 既知リストを更新（掲載終了分は削除せず残す = 再掲載で再通知しない）
    known.update(current)
    save_state(known)
    print(f"known={len(known)} current={len(current)} new={len(new_ids)}")


if __name__ == "__main__":
    main()
