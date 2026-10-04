"""SUUMO 新着監視スクリプト
- SUUMO_URL の検索結果を取得（PC版 / スマホ版どちらの構造にも対応）
- state.json に保存した既知物件と比較し、新着があれば LINE Messaging API（ブロードキャスト）で通知
  ※ 公式アカウントの友だち全員に届く。友だちが自分だけなら実質プッシュ通知と同じ
- 取得失敗の警告は24時間に1回まで（LINE無料枠の節約）
"""
import json
import os
import re
import sys
import time

import requests
from bs4 import BeautifulSoup

URL = os.environ["SUUMO_URL"]
LINE_TOKEN = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
STATE_FILE = "state.json"
MAX_NOTIFY = 10          # 1回の通知で載せる最大件数
WARN_INTERVAL = 24 * 3600  # 警告通知の最短間隔（秒）

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "ja,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

ID_RE = re.compile(r"/chintai/((?:jnc|bc)_\d+)/")
NOISE_RE = re.compile(r"この物件が気になりましたか|写真をもっと見たい|空室状況|問い合わせ|お気に入り")


def fetch(url: str) -> str:
    last = None
    for i in range(3):
        r = requests.get(url, headers=HEADERS, timeout=30)
        last = r
        print(f"fetch attempt {i+1}: status={r.status_code} len={len(r.text)}")
        if r.status_code == 200:
            return r.text
        time.sleep(5 * (i + 1))
    if last is not None:
        last.raise_for_status()
    return ""


def _txt(node, sel):
    el = node.select_one(sel) if node else None
    return el.get_text(" ", strip=True) if el else ""


def _clean_name(s: str) -> str:
    s = (s or "").strip()
    if not s or NOISE_RE.search(s):
        return ""
    return s[:60]


def parse_pc(soup) -> dict:
    rooms = {}
    for item in soup.select("div.cassetteitem"):
        name = _clean_name(_txt(item, ".cassetteitem_content-title"))
        for tr in item.select("tr.js-cassette_link"):
            a = tr.select_one("a[href*='/chintai/']")
            m = ID_RE.search(a["href"]) if a and a.has_attr("href") else None
            if not m:
                continue
            rooms[m.group(1)] = {
                "name": name,
                "rent": _txt(tr, ".cassetteitem_price--rent"),
                "layout": _txt(tr, ".cassetteitem_madori"),
                "area": _txt(tr, ".cassetteitem_menseki"),
                "url": f"https://suumo.jp/chintai/{m.group(1)}/",
            }
    return rooms


def _sp_building_name(item) -> str:
    """スマホ版: 部屋カードの直前にある見出しを建物名とみなす"""
    h = item.find_previous(["h2", "h3"])
    name = _clean_name(h.get_text(" ", strip=True) if h else "")
    # 建物カード内のタイトルらしきクラスも試す
    if not name:
        parent = item.find_parent(class_=re.compile(r"bukken|cassette-wrapper|list-contents"))
        if parent:
            for sel in ("[class*='title']", "[class*='name']", "h2", "h3"):
                name = _clean_name(_txt(parent, sel))
                if name:
                    break
    return name


def parse_sp(soup) -> dict:
    rooms = {}
    for item in soup.select(".juko-cassette"):
        a = item.select_one("a[href*='/chintai/']")
        m = ID_RE.search(a["href"]) if a and a.has_attr("href") else None
        if not m:
            continue
        rooms[m.group(1)] = {
            "name": _sp_building_name(item),
            "rent": _txt(item, ".juko-cassette-chinryo__kakaku"),
            "layout": _txt(item, ".juko-cassette-spec"),
            "area": "",
            "url": f"https://suumo.jp/chintai/{m.group(1)}/",
        }
    return rooms


def parse_fallback(html: str) -> dict:
    """構造が変わっても物件IDだけは拾う最終手段"""
    rooms = {}
    for rid in dict.fromkeys(ID_RE.findall(html)):
        rooms[rid] = {
            "name": "",
            "rent": "",
            "layout": "",
            "area": "",
            "url": f"https://suumo.jp/chintai/{rid}/",
        }
    return rooms


def parse(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    rooms = parse_pc(soup)
    mode = "pc"
    if not rooms:
        rooms = parse_sp(soup)
        mode = "sp"
    if not rooms:
        rooms = parse_fallback(html)
        mode = "fallback"
    print(f"parse: mode={mode} rooms={len(rooms)}")
    return rooms


def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, encoding="utf-8") as f:
            data = json.load(f)
        if "rooms" not in data:  # 旧形式（部屋IDが直下）を変換
            data = {"rooms": data, "meta": {}}
        data.setdefault("meta", {})
        return data
    return {"rooms": {}, "meta": {}}


def save_state(state: dict) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)


def notify_line(text: str) -> None:
    r = requests.post(
        "https://api.line.me/v2/bot/message/broadcast",
        headers={
            "Authorization": f"Bearer {LINE_TOKEN}",
            "Content-Type": "application/json",
        },
        json={"messages": [{"type": "text", "text": text[:4900]}]},
        timeout=30,
    )
    print(f"LINE broadcast: status={r.status_code}")
    if r.status_code != 200:
        print("LINE error:", r.text, file=sys.stderr)
        r.raise_for_status()


def fmt(r: dict) -> str:
    spec = " / ".join(x for x in (r.get("rent"), r.get("layout"), r.get("area")) if x)
    parts = [p for p in (r.get("name"), spec, r.get("url")) if p]
    return "\n" + "\n".join(parts)


def main() -> None:
    state = load_state()
    known, meta = state["rooms"], state["meta"]
    now = int(time.time())

    html = fetch(URL)
    current = parse(html)

    if not current:
        print("WARNING: 物件を1件も取得できませんでした。")
        if now - meta.get("last_warn", 0) > WARN_INTERVAL:
            notify_line("⚠️ SUUMO監視: 物件を取得できませんでした。URLかHTML構造を確認してください。")
            meta["last_warn"] = now
        save_state(state)
        return

    first_run = not known
    new_ids = [rid for rid in current if rid not in known]

    if first_run:
        notify_line(f"✅ SUUMO監視を開始しました。現在 {len(current)} 件を記録。以降は新着のみ通知します。")
    elif new_ids:
        lines = [f"🆕 SUUMO新着 {len(new_ids)}件"]
        lines += [fmt(current[rid]) for rid in new_ids[:MAX_NOTIFY]]
        if len(new_ids) > MAX_NOTIFY:
            lines.append(f"\n…他 {len(new_ids) - MAX_NOTIFY} 件")
        notify_line("\n".join(lines))
    else:
        print("新着なし")

    known.update(current)  # 掲載終了分は残す（再掲載で再通知しない）
    meta["last_run"] = now
    save_state(state)
    print(f"known={len(known)} current={len(current)} new={len(new_ids)}")


if __name__ == "__main__":
    main()
