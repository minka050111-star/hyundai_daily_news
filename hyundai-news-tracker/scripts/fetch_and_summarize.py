#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
현대자동차 대외협력 면접 준비용 - 매일 뉴스 수집/요약 스크립트

1. 네이버 뉴스 검색 API로 여러 키워드를 검색
2. 이미 사용한 기사(seen_links.json)는 영구 제외
3. 키워드 관련도 점수 + 최신순으로 상위 10개 선별
4. (ANTHROPIC_API_KEY가 있으면) Claude API로 핵심내용/면접 인사이트 생성
   (없으면) 네이버가 제공하는 description을 핵심내용으로, 규칙 기반 인사이트로 대체
5. docs/data/{YYYY-MM-DD}.json 으로 저장, docs/data/index.json / seen_links.json 갱신

필요 환경변수:
  NAVER_CLIENT_ID
  NAVER_CLIENT_SECRET
  ANTHROPIC_API_KEY   (선택 — 없으면 규칙 기반 요약으로 자동 대체)
"""

import os
import re
import json
import html
import sys
import datetime
from pathlib import Path
from urllib.parse import quote

import requests

# ----------------------------------------------------------------------
# 설정
# ----------------------------------------------------------------------

NAVER_CLIENT_ID = os.environ.get("NAVER_CLIENT_ID", "")
NAVER_CLIENT_SECRET = os.environ.get("NAVER_CLIENT_SECRET", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "docs" / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
CONTEXT_PATH = Path(__file__).resolve().parent / "context.txt"

SEEN_LINKS_PATH = DATA_DIR / "seen_links.json"
INDEX_PATH = DATA_DIR / "index.json"

TOP_N = 10
FRESH_DAYS = 5          # 이 기간(일) 이내 기사만 신선한 것으로 취급
MAX_PER_QUERY = 30       # 쿼리당 네이버 API에서 가져올 개수

# 검색 쿼리 (JD의 "네트워크/지정학/정책/대관" 관점을 반영)
QUERIES = [
    "현대자동차 관세",
    "현대자동차 대미투자법",
    "현대차그룹 통상",
    "현대자동차 지정학",
    "현대자동차 정책",
    "현대차그룹 대관",
    "현대자동차 해외투자",
    "현대차그룹 외교",
    "현대자동차 수소 정책",
    "현대자동차 공급망",
    "현대차그룹 로비",
    "현대자동차 네트워크",
]

# 관련도 점수용 키워드 (가중치)
RELEVANCE_KEYWORDS = {
    "관세": 3, "통상": 3, "대미투자": 3, "무역": 2, "FTA": 2,
    "정책": 2, "규제": 2, "보조금": 2, "지정학": 3, "안보": 3,
    "공급망": 2, "희토류": 2, "로비": 3, "대관": 3, "외교": 3,
    "정상회의": 2, "네트워크": 2, "투자": 1, "수소": 1, "전기차": 1,
    "IRA": 2, "성김": 3, "성 김": 3, "GPO": 3,
}

HEADERS = {
    "X-Naver-Client-Id": NAVER_CLIENT_ID,
    "X-Naver-Client-Secret": NAVER_CLIENT_SECRET,
}

TAG_RE = re.compile(r"<[^>]+>")


def strip_tags(s: str) -> str:
    return html.unescape(TAG_RE.sub("", s or "")).strip()


def kst_today() -> datetime.date:
    return (datetime.datetime.utcnow() + datetime.timedelta(hours=9)).date()


def parse_pubdate(pub_date: str):
    # 네이버 형식: "Thu, 18 Sep 2026 09:00:00 +0900"
    try:
        return datetime.datetime.strptime(pub_date, "%a, %d %b %Y %H:%M:%S %z")
    except Exception:
        return None


def load_json(path: Path, default):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return default
    return default


def save_json(path: Path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def normalize_link(link: str) -> str:
    return (link or "").split("?")[0].rstrip("/")


def search_naver_news(query: str):
    if not NAVER_CLIENT_ID or not NAVER_CLIENT_SECRET:
        print("ERROR: NAVER_CLIENT_ID / NAVER_CLIENT_SECRET 환경변수가 설정되지 않았습니다.", file=sys.stderr)
        sys.exit(1)
    url = (
        "https://openapi.naver.com/v1/search/news.json"
        f"?query={quote(query)}&display={MAX_PER_QUERY}&sort=date"
    )
    resp = requests.get(url, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    return resp.json().get("items", [])


def score_item(title: str, desc: str) -> int:
    text = f"{title} {desc}"
    score = 0
    for kw, weight in RELEVANCE_KEYWORDS.items():
        if kw in text:
            score += weight
    return score


def collect_candidates(seen_links: set):
    today = kst_today()
    cutoff = today - datetime.timedelta(days=FRESH_DAYS)
    candidates = {}  # normalized link -> item

    for q in QUERIES:
        try:
            items = search_naver_news(q)
        except Exception as e:
            print(f"WARN: 쿼리 '{q}' 검색 실패: {e}", file=sys.stderr)
            continue

        for it in items:
            title = strip_tags(it.get("title", ""))
            desc = strip_tags(it.get("description", ""))
            link = it.get("originallink") or it.get("link") or ""
            norm = normalize_link(link)
            if not norm or norm in seen_links or norm in candidates:
                continue
            if "현대" not in title and "현대" not in desc:
                continue  # 현대차 관련성 없는 결과 제외

            pub = parse_pubdate(it.get("pubDate", ""))
            if pub is not None and pub.date() < cutoff:
                continue  # 너무 오래된 기사 제외

            score = score_item(title, desc)
            candidates[norm] = {
                "title": title,
                "link": link,
                "description": desc,
                "pubDate": it.get("pubDate", ""),
                "score": score,
                "matched_query": q,
            }
    return list(candidates.values())


def rank_top_n(candidates, n=TOP_N):
    def sort_key(c):
        pub = parse_pubdate(c["pubDate"])
        ts = pub.timestamp() if pub else 0
        return (c["score"], ts)

    candidates.sort(key=sort_key, reverse=True)
    return candidates[:n]


def matched_keywords(text: str):
    return [kw for kw in RELEVANCE_KEYWORDS if kw in text]


def fallback_summary(item):
    """ANTHROPIC_API_KEY가 없을 때: 규칙 기반으로 핵심내용/인사이트 생성"""
    core = item["description"] or item["title"]
    if len(core) > 140:
        core = core[:140].rstrip() + "..."

    kws = matched_keywords(f"{item['title']} {item['description']}")
    kw_str = ", ".join(kws[:3]) if kws else "대외협력 전반"
    insight = (
        f"'{kw_str}' 관련 이슈입니다. JD의 네트워크 관리/지정학 대응 항목과 연결해, "
        f"이 사안에서 어떤 이해관계자의 움직임을 어떻게 추적할지 자신의 경험(법안 모니터링, "
        f"국제법 쟁점 분석 등)과 엮어 답변을 준비해보세요."
    )
    return core, insight


def summarize_with_claude(items, context_text):
    """ANTHROPIC_API_KEY가 있을 때: Claude API로 핵심내용/인사이트 일괄 생성"""
    try:
        import anthropic
    except ImportError:
        print("WARN: anthropic 패키지가 없어 규칙 기반 요약으로 대체합니다. (pip install anthropic)", file=sys.stderr)
        return None

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    articles_block = "\n\n".join(
        f"[{i+1}] 제목: {it['title']}\n설명: {it['description']}"
        for i, it in enumerate(items)
    )

    prompt = f"""다음은 채용 지원 직무 JD와 지원자 자소서 컨텍스트입니다:

{context_text}

---

아래는 오늘 수집된 후보 기사 {len(items)}개입니다. 각 기사에 대해 위 컨텍스트를 참고하여
"핵심내용"(1~2문장, 기사의 사실관계 요약)과 "면접 인사이트"(1~2문장, 이 기사를 면접에서
어떻게 활용할 수 있는지 JD/자소서와 연결)를 작성해주세요.

{articles_block}

---

반드시 아래 JSON 배열 형식으로만 답하세요. 다른 설명은 절대 붙이지 마세요:
[
  {{"index": 1, "core": "핵심내용", "insight": "면접 인사이트"}},
  ...
]
"""

    msg = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=4000,
        messages=[{"role": "user", "content": prompt}],
    )
    text = msg.content[0].text.strip()
    # 코드블록으로 감싸져 오는 경우 제거
    text = re.sub(r"^```(json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    try:
        parsed = json.loads(text)
    except Exception as e:
        print(f"WARN: Claude 응답 파싱 실패, 규칙 기반으로 대체: {e}", file=sys.stderr)
        return None

    by_index = {p["index"]: p for p in parsed if "index" in p}
    results = []
    for i, it in enumerate(items):
        p = by_index.get(i + 1)
        if p:
            results.append((p.get("core", ""), p.get("insight", "")))
        else:
            results.append(fallback_summary(it))
    return results


def main():
    seen_links_list = load_json(SEEN_LINKS_PATH, [])
    seen_links = set(seen_links_list)

    candidates = collect_candidates(seen_links)
    top_items = rank_top_n(candidates, TOP_N)

    if not top_items:
        print("오늘 수집된 신규 관련 기사가 없습니다. 종료합니다.")
        return

    context_text = CONTEXT_PATH.read_text(encoding="utf-8") if CONTEXT_PATH.exists() else ""

    summaries = None
    if ANTHROPIC_API_KEY:
        summaries = summarize_with_claude(top_items, context_text)

    final = []
    for i, it in enumerate(top_items):
        if summaries:
            core, insight = summaries[i]
        else:
            core, insight = fallback_summary(it)
        final.append({
            "title": it["title"],
            "link": it["link"],
            "pubDate": it["pubDate"],
            "core": core,
            "insight": insight,
            "matched_query": it["matched_query"],
            "score": it["score"],
        })

    today_str = kst_today().isoformat()
    out_path = DATA_DIR / f"{today_str}.json"
    save_json(out_path, {"date": today_str, "items": final})
    print(f"저장 완료: {out_path} ({len(final)}건)")

    # seen_links 갱신 (영구 제외)
    seen_links.update(normalize_link(it["link"]) for it in top_items)
    save_json(SEEN_LINKS_PATH, sorted(seen_links))

    # index.json 갱신
    index = load_json(INDEX_PATH, [])
    if today_str not in index:
        index.append(today_str)
    index = sorted(set(index), reverse=True)
    save_json(INDEX_PATH, index)


if __name__ == "__main__":
    main()
