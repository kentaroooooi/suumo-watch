"""賃貸 新着監視スクリプト（SUUMO / LIFULL HOME'S / Yahoo!不動産）
- 各サービスの検索結果 URL（環境変数）を取得し、state.json に保存した既知物件と比較
- 新着があれば LINE Messaging API（ブロードキャスト）で 1 通にまとめて通知
- URL が未設定のサービスはスキップ（SUUMO だけでも動く）
- 取得失敗の警告はサービスごとに 24 時間に 1 回まで（LINE 無料枠の節約）
- サービス間は数十秒ずらして取得（同時アクセスを避ける）
"""
import json
import os
import random
import re
import sys
import time

import requests
from bs4 import BeautifulSoup

LINE_TOKEN = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
STATE_FILE = "state.json"
MAX_NOTIFY = 10            # 1回の通知で載せる最大件数（全サービス合計）
WARN_INTERVAL = 24 * 3600  # 警告通知の最短間隔（秒）
GAP_RANGE = (20, 40)       # サービス間の待ち秒数（ランダム）

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "ja,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

NOISE_RE = re.compile(
    r"この物件が気になりましたか|写真をもっと見たい|空室状況|問い合わせ|お気に入り|詳細を見る|もっと見る"
)
RENT_RE = re.compile(r"\d+(?:\.\d+)?\s*万円")
LAYOUT_RE = re.compile(r"ワンルーム|\d[SLDK]{1,4}(?:\+S)?")
AREA_RE = re.compile(r"\d+(?:\.\d+)?\s*(?:m2|m²|㎡)")

# ---------------------------------------------------------------------------
# 監視対象の定義
#   key   : state.json のキー接頭辞（変更禁止：変えると全件が新着扱いになる）
#   label : 通知に表示する名前
#   env   : 検索結果 URL を入れる環境変数（未設定ならスキップ）
#   id_re : 物件 ID を URL から拾う正規表現（group(1) が ID）
#   url   : ID から詳細 URL を組み立てる関数
# ---------------------------------------------------------------------------
SOURCES = [
    {
        "key": "suumo",
        "label": "SUUMO",
        "env": "SUUMO_URL",
        "id_re": re.compile(r"/chintai/((?:jnc|bc)_\d+)/"),
        "url": lambda i: f"https://suumo.jp/chintai/{i}/",
    },
    {
        "key": "homes",
        "label": "HOME'S",
        "env": "HOMES_URL",
        # 部屋ページ /chintai/room/xxxx/ と 建物ページ /chintai/b-12345/ の両方を拾う
        "id_re": re.compile(r"/chintai/(room/[0-9a-zA-Z]+|b-\d+)/"),
        "url": lambda i: f"https://www.homes.co.jp/chintai/{i}/",
    },
    {
        "key": "yahoo",
        "label": "Yahoo!不動産",
        "env": "YAHOO_URL",
        "id_re": re.compile(r"/rent/detail/([0-9a-zA-Z_-]+)/?"),
        "url": lambda i: f"https://realestate.yahoo.co.jp/rent/detail/{i}/",
    },
]


# ---------------------------------------------------------------------------
# 取得
# ---------------------------------------------------------------------------
def fetch(url: str) -> str:
    last = None
    for i in range(3):
        r = requests.get(url, headers=HEADERS, timeout=30)
        last = r
        print(f"  fetch attempt {i+1}: status={r.status_code} len={len(r.text)}")
        if r.status_code == 200:
            return r.text
        if r.status_code in (403, 429):
            # ボット判定・レート制限はリトライで悪化しやすいので打ち切り
            break
        time.sleep(5 * (i + 1))
    if last is not None:
        last.raise_for_status()
    return ""


def _match_id(id_re, href: str):
    m = id_re.search(href or "")
    if not m:
        return None
    # 複数グループの正規表現でも最初に埋まったグループを返す
    for g in m.groups():
        if g:
            return g
    return m.group(0)


# ---------------------------------------------------------------------------
# パース
# ---------------------------------------------------------------------------
def _txt(node, sel):
    el = node.select_one(sel) if node else None
    return el.get_text(" ", strip=True) if el else ""


def _clean_name(s: str) -> str:
    s = re.sub(r"\s+", " ", (s or "")).strip()
    if not s or NOISE_RE.search(s):
        return ""
    return s[:60]


def parse_suumo_pc(soup, src) -> dict:
    rooms = {}
    for item in soup.select("div.cassetteitem"):
        name = _clean_name(_txt(item, ".cassetteitem_content-title"))
        for tr in item.select("tr.js-cassette_link"):
            a = tr.select_one("a[href*='/chintai/']")
            rid = _match_id(src["id_re"], a["href"]) if a and a.has_attr("href") else None
            if not rid:
                continue
            rooms[rid] = {
                "name": name,
                "rent": _txt(tr, ".cassetteitem_price--rent"),
                "layout": _txt(tr, ".cassetteitem_madori"),
                "area": _txt(tr, ".cassetteitem_menseki"),
                "url": src["url"](rid),
            }
    return rooms


def parse_suumo_sp(soup, src) -> dict:
    """スマホ版構造。建物名は確実に取れる手がかりが無いため空にする"""
    rooms = {}
    for item in soup.select(".juko-cassette"):
        a = item.select_one("a[href*='/chintai/']")
        rid = _match_id(src["id_re"], a["href"]) if a and a.has_attr("href") else None
        if not rid:
            continue
        rooms[rid] = {
            "name": "",
            "rent": _txt(item, ".juko-cassette-chinryo__kakaku"),
            "layout": _txt(item, ".juko-cassette-spec"),
            "area": "",
            "url": src["url"](rid),
        }
    return rooms


def parse_generic(soup, src) -> dict:
    """サイト構造に依存しない汎用パース。
    詳細ページへのリンクを起点に、周辺テキストから家賃・間取り・面積を拾う。
    """
    rooms = {}
    for a in soup.find_all("a", href=True):
        rid = _match_id(src["id_re"], a["href"])
        if not rid or rid in rooms:
            continue
        # リンク周辺のテキスト（3階層まで遡る）
        node, text = a, ""
        for _ in range(3):
            if node is None:
                break
            text = node.get_text(" ", strip=True)
            if RENT_RE.search(text):
                break
            node = node.parent
        name = _clean_name(a.get_text(" ", strip=True))
        if len(name) < 3 or RENT_RE.fullmatch(name):
            name = ""
        rent = RENT_RE.search(text)
        layout = LAYOUT_RE.search(text)
        area = AREA_RE.search(text)
        rooms[rid] = {
            "name": name,
            "rent": rent.group(0).replace(" ", "") if rent else "",
            "layout": layout.group(0) if layout else "",
            "area": area.group(0).replace(" ", "") if area else "",
            "url": src["url"](rid),
        }
    return rooms


def parse_fallback(html: str, src) -> dict:
    """構造が変わっても物件 ID だけは拾う最終手段"""
    rooms = {}
    for m in src["id_re"].finditer(html):
        rid = next((g for g in m.groups() if g), None) or m.group(0)
        if rid not in rooms:
            rooms[rid] = {"name": "", "rent": "", "layout": "", "area": "", "url": src["url"](rid)}
    return rooms


def parse(html: str, src) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    rooms, mode = {}, ""
    if src["key"] == "suumo":
        rooms, mode = parse_suumo_pc(soup, src), "pc"
        if not rooms:
            rooms, mode = parse_suumo_sp(soup, src), "sp"
    if not rooms:
        rooms, mode = parse_generic(soup, src), "generic"
    if not rooms:
        rooms, mode = parse_fallback(html, src), "fallback"
    print(f"  parse: mode={mode} rooms={len(rooms)}")
    return rooms


# ---------------------------------------------------------------------------
# 状態
# ---------------------------------------------------------------------------
def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, encoding="utf-8") as f:
            data = json.load(f)
        if "rooms" not in data:  # 最旧形式（部屋 ID が直下）
            data = {"rooms": data, "meta": {}}
        data.setdefault("meta", {})
        # 旧形式（SUUMO 単体・接頭辞なし）→ "suumo:ID" に変換
        rooms = data["rooms"]
        if rooms and not any(":" in k for k in rooms):
            data["rooms"] = {f"suumo:{k}": v for k, v in rooms.items()}
            print("state: 旧形式を suumo: 接頭辞付きに変換しました")
        return data
    return {"rooms": {}, "meta": {}}


def save_state(state: dict) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)


# ---------------------------------------------------------------------------
# 通知
# ---------------------------------------------------------------------------
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


def fmt(label: str, r: dict) -> str:
    spec = " / ".join(x for x in (r.get("rent"), r.get("layout"), r.get("area")) if x)
    head = f"[{label}] {r.get('name') or ''}".rstrip()
    parts = [p for p in (head, spec, r.get("url")) if p]
    return "\n" + "\n".join(parts)


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------
def main() -> None:
    state = load_state()
    known, meta = state["rooms"], state["meta"]
    now = int(time.time())

    active = [s for s in SOURCES if os.environ.get(s["env"])]
    if not active:
        print("監視対象の URL が 1 つも設定されていません。", file=sys.stderr)
        sys.exit(1)
    print("sources:", ", ".join(s["label"] for s in active))

    new_items = []   # (label, room)
    started = []     # 今回初めて監視を開始したサービス
    warnings = []    # 取得失敗したサービス

    for i, src in enumerate(active):
        if i > 0:
            gap = random.randint(*GAP_RANGE)
            print(f"wait {gap}s before next source")
            time.sleep(gap)

        label, key = src["label"], src["key"]
        print(f"== {label}")
        try:
            html = fetch(os.environ[src["env"]])
            current = parse(html, src)
        except Exception as e:  # 1 サービスの失敗で全体を止めない
            print(f"  ERROR: {e}", file=sys.stderr)
            current = {}

        if not current:
            print(f"  WARNING: {label} の物件を 1 件も取得できませんでした。")
            if now - meta.get(f"last_warn:{key}", 0) > WARN_INTERVAL:
                warnings.append(label)
                meta[f"last_warn:{key}"] = now
            continue

        known_ids = {k for k in known if k.startswith(f"{key}:")}
        if not known_ids:
            started.append((label, len(current)))
        else:
            for rid, room in current.items():
                if f"{key}:{rid}" not in known_ids:
                    new_items.append((label, room))

        for rid, room in current.items():  # 掲載終了分は残す（再掲載で再通知しない）
            known[f"{key}:{rid}"] = room
        meta[f"last_run:{key}"] = now
        print(f"  current={len(current)}")

    # --- 通知をまとめる ---
    msgs = []
    for label, n in started:
        msgs.append(f"✅ {label} の監視を開始しました。現在 {n} 件を記録。以降は新着のみ通知します。")
    if new_items:
        lines = [f"🆕 新着 {len(new_items)}件"]
        lines += [fmt(label, room) for label, room in new_items[:MAX_NOTIFY]]
        if len(new_items) > MAX_NOTIFY:
            lines.append(f"\n…他 {len(new_items) - MAX_NOTIFY} 件")
        msgs.append("\n".join(lines))
    for label in warnings:
        msgs.append(f"⚠️ {label} 監視: 物件を取得できませんでした。URL か HTML 構造を確認してください。")

    if msgs:
        notify_line("\n\n".join(msgs))
    else:
        print("新着なし")

    meta["last_run"] = now
    save_state(state)
    print(f"known={len(known)} new={len(new_items)} started={[s for s, _ in started]} warn={warnings}")


if __name__ == "__main__":
    main()
