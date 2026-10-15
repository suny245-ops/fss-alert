"""금융감독원 보도자료 게시판 새 글 → 텔레그램 알림"""
import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

BOARD_NAME = "금감원 보도자료"
BOARD_URL = "https://www.fss.or.kr/fss/bbs/B0000188/list.do?menuNo=200218"
SEEN_FILE = Path("seen.json")
KEEP = 300  # 기억해 둘 최근 글 번호 개수

TOKEN = os.environ["TELEGRAM_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
SEND_TEST = os.environ.get("SEND_TEST") == "1"


def send(text: str) -> None:
    r = requests.post(
        f"https://api.telegram.org/bot{TOKEN}/sendMessage",
        data={"chat_id": CHAT_ID, "text": text},
        timeout=30,
    )
    r.raise_for_status()


def fetch_posts() -> list[dict]:
    r = requests.get(
        BOARD_URL,
        headers={"User-Agent": "Mozilla/5.0 (compatible; fss-alert/1.0)"},
        timeout=30,
    )
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    posts, ids = [], set()
    for a in soup.select('a[href*="view.do"][href*="nttId="]'):
        m = re.search(r"nttId=(\d+)", a["href"])
        if not m or m.group(1) in ids:
            continue
        ids.add(m.group(1))
        date = ""
        row = a.find_parent("tr")
        if row:
            d = re.search(r"\d{4}-\d{2}-\d{2}", row.get_text(" "))
            date = d.group(0) if d else ""
        posts.append({
            "id": m.group(1),
            "title": a.get_text(" ", strip=True),
            "date": date,
            "link": urljoin(BOARD_URL, a["href"]),
        })
    return posts  # 최신 글이 앞쪽


def message(p: dict) -> str:
    return f"[{BOARD_NAME}] {p['date']}\n{p['title']}\n{p['link']}"


def main() -> None:
    try:
        posts = fetch_posts()
    except Exception as e:
        send(f"⚠️ {BOARD_NAME} 확인 실패: {e}")
        sys.exit(1)

    if not posts:
        send(f"⚠️ {BOARD_NAME}: 글 목록을 찾지 못했어요. 사이트 구조가 바뀌었을 수 있어요.")
        sys.exit(1)

    if SEND_TEST:
        send("🔔 시험 발송입니다. 가장 최근 글:\n\n" + message(posts[0]))

    if not SEEN_FILE.exists():
        # 첫 실행: 지금 있는 글은 '이미 본 글'로 기록만 하고 알림은 보내지 않음
        SEEN_FILE.write_text(json.dumps([p["id"] for p in posts], indent=0))
        send(f"✅ {BOARD_NAME} 감시를 시작했어요. 새 글이 올라오면 알려드릴게요.")
        return

    seen = json.loads(SEEN_FILE.read_text())
    new = [p for p in posts if p["id"] not in seen]
    for p in reversed(new):  # 오래된 글부터 순서대로
        send("🆕 " + message(p))

    if new:
        seen = [p["id"] for p in new] + seen
        SEEN_FILE.write_text(json.dumps(seen[:KEEP], indent=0))
    print(f"글 {len(posts)}개 확인, 새 글 {len(new)}개")


if __name__ == "__main__":
    main()
