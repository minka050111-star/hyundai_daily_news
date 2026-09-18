#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
현대자동차 대외협력 면접 준비용 - 매일 뉴스 수집/요약 스크립트

1. 네이버 뉴스 검색 API로 여러 키워드를 검색
   - HYUNDAI_QUERIES: 현대차/현대차그룹을 직접 언급하는 검색어
   - INDUSTRY_QUERIES: 현대차를 언급하지 않아도, 자동차 산업 전반의
     관세/통상/정책/지정학 이슈를 다루는 검색어 (현대차 전략에 영향 줄 수 있는 배경 뉴스)
2. 이미 사용한 기사(seen_links.json)는 영구 제외
3. 관련도 점수(MIN_SCORE 미만 제외, 현대차/기아 직접 언급 시 보너스 점수)로 필터링 후
   "현대자동차" / "자동차 산업" / "국제 정세" 세 카테고리로 분류 (categorize 함수) —
   "현대"라는 단어가 꼭 들어가야 하는 건 아님. 카테고리별로 각각 관련도·최신순 상위
   TOP_N_PER_CATEGORY(기본 10)개씩 선별 (최대 하루 30건)
4. (ANTHROPIC_API_KEY가 있으면) Claude API로 개별 기사 핵심내용/면접 인사이트 +
   카테고리별 종합 요약 생성 (없으면) 규칙 기반으로 자동 대체
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

TOP_N_PER_CATEGORY = 10  # 카테고리(현대자동차/자동차 산업/국제 정세)별로 뽑을 기사 수
FRESH_DAYS = 5          # 이 기간(일) 이내 기사만 신선한 것으로 취급
MAX_PER_QUERY = 50       # 쿼리당 네이버 API에서 가져올 개수 (카테고리별로 10개씩 채우려면
                          # 후보 풀이 더 넉넉해야 하므로 기존 30 → 50으로 확대)
MIN_SCORE = 3            # 이 점수 미만이면 "현대차 관련성 낮음"으로 보고 제외
                          # (예전엔 제목/설명에 "현대"가 꼭 들어가야 했지만,
                          #  이제는 "현대"가 없어도 자동차 산업/통상/지정학 전반에서
                          #  현대차 대외협력 전략에 영향 줄 만한 기사면 통과시킴)

# 검색 쿼리 — 두 그룹으로 구성
# 1) 현대차/현대차그룹을 직접 언급하는 쿼리 (직접 관련 뉴스)
HYUNDAI_QUERIES = [
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

# 2) 현대차를 직접 언급하지 않아도, 자동차 산업 전반의 정책/통상/지정학
#    이슈를 다루는 쿼리 (현대차 대외협력 전략에 영향 줄 수 있는 배경 뉴스)
INDUSTRY_QUERIES = [
    "자동차 관세",
    "자동차 업계 통상",
    "자동차 산업 정책",
    "전기차 보조금 정책",
    "한미 FTA 자동차",
    "자동차 공급망 리스크",
    "희토류 수출 통제",
    "인플레이션감축법 자동차",
    "EU 탄소국경조정 자동차",
    "글로벌 자동차 지정학",
    "미국 자동차 관세 협상",
    "반도체 수출통제 자동차",
]

QUERIES = HYUNDAI_QUERIES + INDUSTRY_QUERIES

# 관련도 점수용 키워드 (가중치)
RELEVANCE_KEYWORDS = {
    "관세": 3, "통상": 3, "대미투자": 3, "무역": 2, "FTA": 2,
    "정책": 2, "규제": 2, "보조금": 2, "지정학": 3, "안보": 3,
    "공급망": 2, "희토류": 2, "로비": 3, "대관": 3, "외교": 3,
    "정상회의": 2, "네트워크": 2, "투자": 1, "수소": 1, "전기차": 1,
    "IRA": 2, "성김": 3, "성 김": 3, "GPO": 3,
    "반도체": 1, "탄소국경": 2, "수출통제": 2, "제재": 2,
}

# 현대차/기아를 직접 언급하면 주는 보너스 점수 (필수는 아니지만 우선순위를 높여줌)
DIRECT_MENTION_KEYWORDS = ["현대차", "현대자동차", "현대차그룹", "기아"]
DIRECT_MENTION_BONUS = 2

# 대시보드 카테고리 분류용 키워드
# 1) 제목/설명에 현대차/기아가 직접 언급되면 무조건 "현대자동차"
# 2) 그렇지 않으면 관세/통상/외교/안보 등 "국제 정세" 성격 키워드와
#    정책/보조금/기술 등 "자동차 산업" 성격 키워드의 매칭 개수를 비교해 분류
CATEGORIES = ["현대자동차", "자동차 산업", "국제 정세"]
GEO_KEYWORDS = [
    "관세", "통상", "협상", "대미투자", "무역", "FTA", "지정학", "안보",
    "희토류", "공급망", "로비", "대관", "외교", "정상회의", "네트워크",
    "IRA", "성김", "성 김", "GPO", "탄소국경", "수출통제", "제재",
]
INDUSTRY_KEYWORDS = ["정책", "규제", "보조금", "투자", "수소", "전기차", "반도체", "산업"]


def categorize(title: str, desc: str) -> str:
    text = f"{title} {desc}"
    if any(kw in text for kw in DIRECT_MENTION_KEYWORDS):
        return "현대자동차"
    geo_score = sum(1 for kw in GEO_KEYWORDS if kw in text)
    industry_score = sum(1 for kw in INDUSTRY_KEYWORDS if kw in text)
    if geo_score > industry_score:
        return "국제 정세"
    return "자동차 산업"

# 2026년 네이버 뉴스 검색 API가 NAVER API HUB(NCP)로 이관되면서
# 요청 주소와 인증 헤더 이름이 변경되었습니다.
NAVER_NEWS_API_URL = "https://naverapihub.apigw.ntruss.com/search/v1/news"
HEADERS = {
    "X-NCP-APIGW-API-KEY-ID": NAVER_CLIENT_ID,
    "X-NCP-APIGW-API-KEY": NAVER_CLIENT_SECRET,
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
        f"{NAVER_NEWS_API_URL}"
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
    if any(kw in text for kw in DIRECT_MENTION_KEYWORDS):
        score += DIRECT_MENTION_BONUS
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

            pub = parse_pubdate(it.get("pubDate", ""))
            if pub is not None and pub.date() < cutoff:
                continue  # 너무 오래된 기사 제외

            score = score_item(title, desc)
            if score < MIN_SCORE:
                continue  # 자동차/통상/지정학 관련성이 너무 낮은 결과 제외
                          # (현대차를 직접 언급하지 않아도 여기서 걸러지지 않으면 후보에 포함됨)

            candidates[norm] = {
                "title": title,
                "link": link,
                "description": desc,
                "pubDate": it.get("pubDate", ""),
                "score": score,
                "matched_query": q,
                "category": categorize(title, desc),
            }
    return list(candidates.values())


def _sort_key(c):
    pub = parse_pubdate(c["pubDate"])
    ts = pub.timestamp() if pub else 0
    return (c["score"], ts)


def rank_top_n_per_category(candidates, n=TOP_N_PER_CATEGORY):
    """카테고리(현대자동차/자동차 산업/국제 정세)별로 각각 상위 n개씩 선별.
    (기존에는 전체 통틀어 top 10만 뽑았는데, 카테고리마다 최소 n개를 채우기 위해
    카테고리별로 별도 선별 후 합침)"""
    by_category = {cat: [] for cat in CATEGORIES}
    for c in candidates:
        by_category.setdefault(c["category"], []).append(c)

    selected = []
    for cat in CATEGORIES:
        group = sorted(by_category.get(cat, []), key=_sort_key, reverse=True)
        selected.extend(group[:n])

    # 전체 목록은 관련도/최신순으로 다시 정렬 (카테고리 구분 없이 "전체" 탭에서 보기 좋게)
    selected.sort(key=_sort_key, reverse=True)
    return selected


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


def fallback_category_summary(category: str, items: list, overall: bool = False) -> str:
    """ANTHROPIC_API_KEY가 없을 때: 카테고리별(또는 전체) 규칙 기반 종합 요약"""
    if not items:
        return f"오늘 '{category}' 카테고리에 해당하는 기사가 없습니다."

    kw_counts = {}
    for it in items:
        for kw in matched_keywords(f"{it['title']} {it['core']}"):
            kw_counts[kw] = kw_counts.get(kw, 0) + 1
    top_kws = sorted(kw_counts, key=lambda k: -kw_counts[k])[:4]
    kw_str = ", ".join(top_kws) if top_kws else "대외협력 전반"
    latest_title = items[0]["title"]

    if overall:
        cat_counts = {}
        for it in items:
            cat_counts[it["category"]] = cat_counts.get(it["category"], 0) + 1
        breakdown = ", ".join(f"{c} {n}건" for c, n in cat_counts.items())
        return (
            f"오늘은 총 {len(items)}건의 기사가 수집되었습니다 ({breakdown}). "
            f"주요 키워드는 {kw_str}이며, 가장 최근 기사는 '{latest_title}'입니다."
        )
    return (
        f"오늘 '{category}' 카테고리에는 총 {len(items)}건의 기사가 수집되었습니다. "
        f"주요 키워드는 {kw_str}이며, 가장 최근 기사는 '{latest_title}'입니다."
    )


def summarize_categories_with_claude(items_by_category: dict, context_text: str):
    """ANTHROPIC_API_KEY가 있을 때: 카테고리별 종합 요약을 Claude API로 한 번에 생성
    (개별 기사 요약이 아니라, 그 카테고리에 모인 기사들을 묶어서 보는 종합 시각)"""
    try:
        import anthropic
    except ImportError:
        print("WARN: anthropic 패키지가 없어 규칙 기반 카테고리 요약으로 대체합니다.", file=sys.stderr)
        return None

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    blocks = []
    for cat, items in items_by_category.items():
        if not items:
            continue
        lines = "\n".join(f"- {it['title']}: {it['core']}" for it in items)
        blocks.append(f"[{cat}] ({len(items)}건)\n{lines}")
    articles_block = "\n\n".join(blocks)

    if not articles_block:
        return None

    prompt = f"""다음은 채용 지원 직무 JD와 지원자 자소서 컨텍스트입니다:

{context_text}

---

아래는 오늘 수집된 기사들을 카테고리별로 정리한 목록입니다.

{articles_block}

---

각 카테고리별로, 오늘 그 카테고리에 모인 기사들을 종합했을 때 어떤 흐름/맥락으로 읽히는지
3~4문장으로 요약해주세요 (개별 기사를 하나씩 재서술하지 말고, "오늘 이 카테고리 전체를 보면
~한 흐름이다"는 종합적 시각으로). 가능하면 위 JD/자소서 맥락과 연결되는 시사점도 한 문장 포함하세요.

반드시 아래 JSON 객체 형식으로만 답하세요. 카테고리 이름은 위 목록의 대괄호 안 이름과
정확히 동일하게 쓰고, 목록에 없는 카테고리는 포함하지 마세요. 다른 설명은 절대 붙이지 마세요:
{{"현대자동차": "요약...", "자동차 산업": "요약...", "국제 정세": "요약..."}}
"""

    msg = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=1500,
        messages=[{"role": "user", "content": prompt}],
    )
    text = msg.content[0].text.strip()
    text = re.sub(r"^```(json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    try:
        return json.loads(text)
    except Exception as e:
        print(f"WARN: 카테고리 요약 파싱 실패, 규칙 기반으로 대체: {e}", file=sys.stderr)
        return None


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
    top_items = rank_top_n_per_category(candidates, TOP_N_PER_CATEGORY)

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
            "category": it["category"],
        })

    # 카테고리별(현대자동차/자동차 산업/국제 정세) + 전체 종합 요약 생성
    items_by_category = {cat: [it for it in final if it["category"] == cat] for cat in CATEGORIES}

    category_summaries = None
    if ANTHROPIC_API_KEY:
        category_summaries = summarize_categories_with_claude(items_by_category, context_text)
    if not category_summaries:
        category_summaries = {}
    for cat in CATEGORIES:
        if not category_summaries.get(cat):
            category_summaries[cat] = fallback_category_summary(cat, items_by_category[cat])
    category_summaries["전체"] = fallback_category_summary("전체", final, overall=True)

    today_str = kst_today().isoformat()
    out_path = DATA_DIR / f"{today_str}.json"
    save_json(out_path, {
        "date": today_str,
        "items": final,
        "category_summaries": category_summaries,
    })
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
