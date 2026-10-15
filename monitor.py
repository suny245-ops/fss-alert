"""금융감독원 게시판 새 글 → 텔레그램 알림 (여러 게시판 지원)"""
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

# ──────────────────────────────────────────────────────────────
# 감시할 게시판 목록
#   name    : 알림에 표시될 이름 (한 번 정하면 바꾸지 마세요. 기록이 이 이름으로 저장됩니다)
#   url     : 목록 페이지 주소. {start}, {today} 는 실행할 때 날짜로 자동 채워짐
#   id_keys : 글 링크에서 글을 구분하는 값의 이름
# ──────────────────────────────────────────────────────────────
BOARDS = [
    {
        "name": "금감원 보도자료",
        "url": "https://www.fss.or.kr/fss/bbs/B0000188/list.do?menuNo=200218",
        "id_keys": ["nttId"],
    },
    {
        "name": "금감원 검사결과제재",
        "url": "https://www.fss.or.kr/fss/job/openInfo/list.do?menuNo=200476"
               "&pageIndex=1&sdate={start}&edate={today}&searchCnd=4&searchWrd=",
        "id_keys": ["examMgmtNo", "emOpenSeq"],
    },
]

SEEN_FILE = Path("seen.json")
KEEP = 300  # 게시판별로 기억해 둘 최근 글 개수
KST = timezone(timedelta(hours=9))

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


def board_url(board: dict) -> str:
    today = datetime.now(KST).date()
    return board["url"].format(
        today=today.isoformat(),
        start=(today - timedelta(days=365)).isoformat(),
    )


def find_date(text: str) -> str:
    m = re.search(r"\d{4}-\d{2}-\d{2}", text)
    if m:
        return m.group(0)
    m = re.search(r"\b(20\d{2})(\d{2})(\d{2})\b", text)
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else ""


def fetch_posts(board: dict) -> list[dict]:
    url = board_url(board)
    r = requests.get(
        url,
        headers={"User-Agent": "Mozilla/5.0 (compatible; fss-alert/1.0)"},
        timeout=30,
    )
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    posts, ids = [], set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "view.do" not in href:
            continue
        q = parse_qs(urlparse(href).query)
        if not all(q.get(k) for k in board["id_keys"]):
            continue
        pid = "-".join(q[k][0] for k in board["id_keys"])
        if pid in ids:
            continue
        ids.add(pid)

        row = a.find_parent("tr")
        row_text = row.get_text(" ", strip=True) if row else ""
        title = a.get_text(" ", strip=True)
        if row and (len(title) < 2 or title in ("내용보기", "보기", "상세보기")):
            # 링크 글자가 '내용보기' 같은 버튼이면, 그 줄의 다른 칸에서 제목을 찾음
            cells = [td.get_text(" ", strip=True) for td in row.find_all("td")]
            cands = [c for c in cells
                     if c and not c.isdigit()
                     and not re.fullmatch(r"\d{8}|\d{4}-\d{2}-\d{2}", c)
                     and c not in ("내용보기", "보기", "상세보기")]
            title = " · ".join(cands[:2]) if cands else "(제목 없음)"
        posts.append({
            "id": pid,
            "title": title,
            "date": find_date(row_text),
            "link": urljoin(url, href),
        })
    return posts  # 최신 글이 앞쪽


def message(name: str, p: dict) -> str:
    return f"[{name}] {p['date']}\n{p['title']}\n{p['link']}"


def load_seen() -> dict:
    if not SEEN_FILE.exists():
        return {}
    data = json.loads(SEEN_FILE.read_text())
    if isinstance(data, list):  # 예전 형식(보도자료만 있던 때) → 새 형식으로 변환
        return {"금감원 보도자료": data}
    return data


def main() -> None:
    seen = load_seen()
    failed = False

    for board in BOARDS:
        name = board["name"]
        try:
            posts = fetch_posts(board)
        except Exception as e:
            send(f"⚠️ {name} 확인 실패: {e}")
            failed = True
            continue

        if not posts:
            send(f"⚠️ {name}: 글 목록을 찾지 못했어요. 사이트 구조가 바뀌었을 수 있어요.")
            failed = True
            continue

        if SEND_TEST:
            send("🔔 시험 발송입니다. 가장 최근 글:\n\n" + message(name, posts[0]))

        if name not in seen:
            # 처음 감시하는 게시판: 지금 있는 글은 기록만 하고 알림은 보내지 않음
            seen[name] = [p["id"] for p in posts]
            send(f"✅ {name} 감시를 시작했어요. 새 글이 올라오면 알려드릴게요.")
            continue

        new = [p for p in posts if p["id"] not in seen[name]]
        for p in reversed(new):  # 오래된 글부터 순서대로
            send("🆕 " + message(name, p))
        if new:
            seen[name] = ([p["id"] for p in new] + seen[name])[:KEEP]
        print(f"{name}: 글 {len(posts)}개 확인, 새 글 {len(new)}개")

    SEEN_FILE.write_text(json.dumps(seen, ensure_ascii=False, indent=1))
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
