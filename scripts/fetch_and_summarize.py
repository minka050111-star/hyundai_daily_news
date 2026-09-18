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
import difflib
import datetime
from pathlib import Path
from urllib.parse import quote, urlparse

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

# 검색 쿼리 — 세 그룹으로 구성 (아래 CATEGORIES 3개와 1:1로 매칭되는 배경 검색어)
# 1) 현대차그룹 고유 기업·전략·신사업 관련 검색어 (경영/이벤트, 생산거점/신차,
#    미래기술/신사업)
HYUNDAI_QUERIES = [
    "현대차 제네시스",
    "현대차 호세 무뇨스",
    "현대차 정의선",
    "현대차 Investor Day",
    "현대차 주주환원",
    "현대차 밸류업",
    "현대차 HMGMA",
    "현대차 아이오닉",
    "현대차 캐스퍼EV",
    "현대차 넥쏘",
    "현대차 EREV",
    "현대차 TMED",
    "현대차 마그마",
    "현대차 Pleos",
    "현대차 SDV",
    "현대차 HTWO",
    "현대차 보스턴다이내믹스",
    "현대차 포티투닷",
    "현대차 슈퍼널",
    "현대차 모셔널",
]

# 2) 현대차를 직접 언급하지 않아도, 자동차 산업 전반의 시장/기술/환경규제
#    트렌드를 다루는 검색어 (전동화·파워트레인, 환경·연비규제, 미래 모빌리티)
INDUSTRY_QUERIES = [
    "전기차 캐즘",
    "하이브리드차 판매",
    "EREV 주행거리연장",
    "LFP 배터리",
    "NCM 배터리",
    "배터리 화재 전기차",
    "BaaS 배터리구독",
    "IONNA 충전",
    "NACS 충전표준",
    "Euro7 배출규제",
    "EPA 배출가스 기준",
    "CAFE 연비규제",
    "탄소중립 자동차",
    "RE100 자동차",
    "SDV 소프트웨어 자동차",
    "자율주행 로보택시",
    "피지컬 AI 자동차",
    "AI 자율제조 공장",
]

# 3) 통상/지정학/글로벌 규제 — 대외협력 직무에서 가장 예의주시해야 하는 영역
#    (통상·관세, 친환경·통상규제, 공급망·노동인권규제, 지정학·글로벌경쟁)
GEO_QUERIES = [
    "보편관세 자동차",
    "보호무역주의 자동차",
    "한미 자동차 관세",
    "미국 신정부 통상",
    "트럼프 자동차 관세",
    "IRA 전기차 보조금",
    "CBAM 탄소국경조정",
    "EUDR 산림전용방지",
    "EU 배터리법",
    "IAA 유럽 산업가속화법",
    "UFLPA 강제노동",
    "CSDDD 공급망실사",
    "책임광물 리튬 니켈",
    "미중 갈등 자동차",
    "중국 전기차 해외진출",
    "러시아 우크라이나 전쟁 자동차",
    "중동 지정학 리스크",
    "공급망 다변화 자동차",
]

QUERIES = HYUNDAI_QUERIES + INDUSTRY_QUERIES + GEO_QUERIES

# 관련도 점수용 키워드 (가중치) — 카테고리 대표성이 강할수록 높은 가중치
RELEVANCE_KEYWORDS = {
    # --- 현대자동차: 경영 & 주요 이벤트 ---
    "제네시스": 3, "호세 무뇨스": 3, "정의선": 3, "Investor Day": 2,
    "주주서한": 2, "주주환원": 2, "밸류업": 2,
    # --- 현대자동차: 생산거점 & 신차/파워트레인 ---
    "HMGMA": 3, "메타플랜트": 3, "아이오닉": 2, "IONIQ": 2, "캐스퍼": 1,
    "넥쏘": 3, "EREV": 2, "TMED": 2, "마그마": 2, "LCV": 1, "ST1": 2,
    # --- 현대자동차: 미래기술 & 그룹 신사업 ---
    "Pleos": 3, "플레오스": 3, "SDV": 2, "HTWO": 3, "보스턴다이내믹스": 3,
    "보스턴 다이내믹스": 3, "아틀라스": 1, "포티투닷": 3, "슈퍼널": 3,
    "모셔널": 3, "웨이모": 1,
    # --- 자동차 산업: 전동화 & 파워트레인 트렌드 ---
    "전기차 캐즘": 3, "캐즘": 2, "하이브리드": 2, "HEV": 1, "LFP": 2,
    "NCM": 2, "배터리 화재": 2, "BaaS": 2, "IONNA": 2, "NACS": 2,
    # --- 자동차 산업: 환경 & 연비 규제 ---
    "Euro7": 2, "유로7": 2, "EPA": 2, "플릿": 1, "CAFE": 2, "탄소중립": 2,
    "RE100": 2, "LCA": 1,
    # --- 자동차 산업: 미래 모빌리티 & 기술 ---
    "E/E 아키텍처": 2, "OTA": 1, "자율주행": 1, "로보택시": 2,
    "피지컬 AI": 2, "SDF": 2,
    # --- 국제정세: 통상 & 관세 리스크 ---
    "보편관세": 3, "보호무역": 2, "한미 무역": 2, "한미 관세": 3,
    "트럼프 관세": 3, "자동차 관세": 3, "관세": 2, "통상": 2, "무역": 1,
    # --- 국제정세: 친환경 & 통상 규제 ---
    "IRA": 2, "CBAM": 3, "EUDR": 2, "EU Battery Regulation": 2,
    "배터리법": 2, "IAA": 2,
    # --- 국제정세: 공급망 & 노동/인권 규제 ---
    "UFLPA": 3, "CSDDD": 3, "FLR": 2, "책임광물": 2, "리튬": 1, "니켈": 1,
    "코발트": 1, "흑연": 1, "공급망 재편": 2, "공급망": 2,
    # --- 국제정세: 지정학 & 글로벌 경쟁 ---
    "미중 갈등": 3, "NEV": 2, "중국 전기차": 2, "러우 전쟁": 2,
    "우크라이나": 1, "중동": 1, "지정학": 3, "공급망 다변화": 2,
    # --- 기존 대외협력 맥락 키워드 (계속 유지) ---
    "대미투자": 2, "FTA": 2, "정책": 2, "규제": 1, "보조금": 1, "안보": 2,
    "희토류": 2, "로비": 3, "대관": 3, "외교": 3, "정상회의": 2,
    "네트워크": 1, "투자": 1, "수소": 1, "전기차": 1, "성김": 3, "성 김": 3,
    "GPO": 3, "반도체": 1, "탄소국경": 2, "수출통제": 2, "제재": 2,
}

# 현대차그룹 고유 기업/브랜드/인물이 언급되면 무조건 "현대자동차" 카테고리로 분류
DIRECT_MENTION_KEYWORDS = [
    "현대차", "현대자동차", "현대차그룹", "기아", "제네시스",
    "호세 무뇨스", "정의선", "HMGMA", "아이오닉", "IONIQ", "캐스퍼EV",
    "넥쏘", "마그마", "Pleos", "플레오스", "HTWO", "포티투닷", "슈퍼널",
    "모셔널", "보스턴다이내믹스", "보스턴 다이내믹스", "ST1",
]
DIRECT_MENTION_BONUS = 2

# 대시보드 카테고리 분류용 키워드
# 1) 위 DIRECT_MENTION_KEYWORDS(현대차그룹 고유 기업/브랜드/인물)가 언급되면
#    무조건 "현대자동차"
# 2) 그렇지 않으면 "국제 정세"(통상/관세/지정학/글로벌 규제) 성격 키워드와
#    "자동차 산업"(시장/기술/환경규제) 성격 키워드의 매칭 개수를 비교해 분류
CATEGORIES = ["현대자동차", "자동차 산업", "국제 정세"]

GEO_KEYWORDS = [
    "보편관세", "보호무역", "한미 무역", "한미 관세", "트럼프 관세",
    "자동차 관세", "관세", "통상", "무역", "IRA", "CBAM", "EUDR",
    "EU Battery Regulation", "배터리법", "IAA", "UFLPA", "CSDDD", "FLR",
    "책임광물", "리튬", "니켈", "코발트", "흑연", "공급망 재편", "공급망",
    "미중 갈등", "NEV", "중국 전기차", "러우 전쟁", "우크라이나", "중동",
    "지정학", "공급망 다변화", "대미투자", "FTA", "안보", "희토류", "로비",
    "대관", "외교", "정상회의", "네트워크", "성김", "성 김", "GPO",
    "탄소국경", "수출통제", "제재", "협상",
]
INDUSTRY_KEYWORDS = [
    "전기차 캐즘", "캐즘", "하이브리드", "HEV", "LFP", "NCM", "배터리 화재",
    "BaaS", "IONNA", "NACS", "Euro7", "유로7", "EPA", "플릿", "CAFE",
    "탄소중립", "RE100", "LCA", "SDV", "E/E 아키텍처", "OTA", "자율주행",
    "로보택시", "피지컬 AI", "SDF", "정책", "규제", "보조금", "투자",
    "수소", "전기차", "반도체", "산업",
]


def categorize(title: str, desc: str) -> str:
    text = f"{title} {desc}"
    if any(kw in text for kw in DIRECT_MENTION_KEYWORDS):
        return "현대자동차"
    geo_score = sum(1 for kw in GEO_KEYWORDS if kw in text)
    industry_score = sum(1 for kw in INDUSTRY_KEYWORDS if kw in text)
    if geo_score > industry_score:
        return "국제 정세"
    return "자동차 산업"


# 규모 있는 언론사 우선순위용 — 기사 원문 도메인이 아래에 해당하면 순위 산정 시 보너스 점수
# (완전히 걸러내는 건 아니고, 같은 카테고리 안에서 이런 매체 기사가 먼저 뽑히도록 가중치만 줌)
MAJOR_OUTLET_DOMAINS = {
    "yna.co.kr": "연합뉴스",
    "sedaily.com": "서울경제",
    "hankyung.com": "한국경제",
    "chosun.com": "조선일보",
    "joongang.co.kr": "중앙일보",
    "joins.com": "중앙일보",
    "donga.com": "동아일보",
    "kbs.co.kr": "KBS",
    "sbs.co.kr": "SBS",
    "imbc.com": "MBC",
    "mbc.co.kr": "MBC",
    "mk.co.kr": "매일경제",
    "hani.co.kr": "한겨레",
    "yonhapnews.co.kr": "연합뉴스",
}
MAJOR_OUTLET_BONUS = 4


def extract_domain(link: str) -> str:
    try:
        netloc = urlparse(link).netloc.lower()
        return netloc[4:] if netloc.startswith("www.") else netloc
    except Exception:
        return ""


def match_major_outlet(domain: str):
    for base, name in MAJOR_OUTLET_DOMAINS.items():
        if domain == base or domain.endswith("." + base):
            return name
    return None


# 같은 이슈를 여러 매체가 비슷하게 받아쓴 "중복 기사"를 걸러내기 위한 제목 유사도 체크
# (완전 일치가 아니라, [단독]/[속보] 같은 태그를 떼고 핵심 단어 구성이 얼마나 겹치는지로 판단)
_TITLE_TAG_RE = re.compile(r"^\s*\[[^\]]{1,12}\]\s*")
_TITLE_NOISE_RE = re.compile(r"[^\w가-힣]+")


def _normalize_title(title: str) -> str:
    t = _TITLE_TAG_RE.sub("", title or "")
    t = _TITLE_NOISE_RE.sub(" ", t)
    return t.strip()


def is_similar_title(a: str, b: str, threshold: float = 0.6) -> bool:
    na, nb = _normalize_title(a), _normalize_title(b)
    if not na or not nb:
        return False
    return difflib.SequenceMatcher(None, na, nb).ratio() >= threshold

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

            domain = extract_domain(link)
            outlet_name = match_major_outlet(domain)

            candidates[norm] = {
                "title": title,
                "link": link,
                "description": desc,
                "pubDate": it.get("pubDate", ""),
                "score": score,
                "matched_query": q,
                "category": categorize(title, desc),
                "source": outlet_name or domain,
                "is_major_outlet": outlet_name is not None,
            }
    return list(candidates.values())


def _sort_key(c):
    pub = parse_pubdate(c["pubDate"])
    ts = pub.timestamp() if pub else 0
    bonus = MAJOR_OUTLET_BONUS if c.get("is_major_outlet") else 0
    return (c["score"] + bonus, ts)


def rank_top_n_per_category(candidates, n=TOP_N_PER_CATEGORY):
    """카테고리(현대자동차/자동차 산업/국제 정세)별로 각각 상위 n개씩 선별.
    (기존에는 전체 통틀어 top 10만 뽑았는데, 카테고리마다 최소 n개를 채우기 위해
    카테고리별로 별도 선별 후 합침)

    선별 과정에서:
    - 규모 있는 언론사(연합뉴스/서울경제/한국경제/조선일보/중앙일보/동아일보/KBS/SBS/MBC 등)
      기사가 점수 동점권에서 먼저 뽑히도록 _sort_key에 보너스가 반영되어 있음
    - 이미 뽑힌 기사와 제목이 비슷한(같은 이슈를 여러 매체가 받아쓴) 기사는 건너뜀"""
    by_category = {cat: [] for cat in CATEGORIES}
    for c in candidates:
        by_category.setdefault(c["category"], []).append(c)

    selected = []
    for cat in CATEGORIES:
        group = sorted(by_category.get(cat, []), key=_sort_key, reverse=True)
        picked = []
        for c in group:
            if len(picked) >= n:
                break
            if any(is_similar_title(c["title"], p["title"]) for p in picked):
                continue  # 이미 뽑힌 기사와 내용이 겹치는 것으로 판단 → 제외
            picked.append(c)
        selected.extend(picked)

    # 전체 목록은 관련도/최신순으로 다시 정렬 (카테고리 구분 없이 "전체" 탭에서 보기 좋게)
    selected.sort(key=_sort_key, reverse=True)
    return selected


def matched_keywords(text: str):
    return [kw for kw in RELEVANCE_KEYWORDS if kw in text]


# ANTHROPIC_API_KEY가 없을 때 쓰는 규칙 기반 인사이트 템플릿.
# 매칭된 키워드의 "성격"에 따라 다른 문장을 골라 쓰기 때문에, 모든 기사에 똑같은
# 문장이 반복되지 않고 최소한 주제별로는 다른 인사이트가 나오도록 함.
# (진짜 기사별 맞춤 인사이트를 원하면 ANTHROPIC_API_KEY 등록이 필요 — 이건 그 대체용)
INSIGHT_THEME_TEMPLATES = [
    (
        {"관세", "통상", "FTA", "대미투자", "무역"},
        "관세·통상 조건은 발표 이후에도 세부 수치·적용 시점이 계속 바뀌므로, "
        "이 기사의 조건이 이전 보도와 어떻게 달라졌는지 짚어두면 면접에서 "
        "최신 동향을 정확히 설명할 수 있습니다.",
    ),
    (
        {"지정학", "안보", "공급망", "희토류", "수출통제", "제재"},
        "지정학 리스크가 구체적으로 어떤 경로(생산·수출·원자재 조달)로 사업에 "
        "영향을 주는지 이 기사를 근거로 설명하면, 추상적인 답변을 피할 수 있습니다.",
    ),
    (
        {"대관", "로비", "외교", "네트워크", "정상회의", "GPO", "성김", "성 김"},
        "이 사례가 보여주는 실제 대외협력 활동 방식(어떤 채널·인물을 통해 움직였는지)을 "
        "정리해두면, 지원 직무를 구체적으로 이해하고 있다는 인상을 줄 수 있습니다.",
    ),
    (
        {"정책", "규제", "보조금", "수소", "전기차", "IRA", "탄소국경"},
        "정책 방향 변화가 회사에 실제로 어떤 영향을 주는지 이 기사로 설명하면, "
        "정책 모니터링이 왜 대외협력의 핵심 업무인지 답변에 근거를 더할 수 있습니다.",
    ),
]
DEFAULT_INSIGHT = (
    "이 사안이 향후 회사의 대외협력 대응(네트워크·정책 모니터링)에 주는 시사점을 "
    "한 문장으로 정리해 면접 답변 소재로 활용해보세요."
)


def build_fallback_insight(kws: list) -> str:
    kw_set = set(kws)
    for theme_kws, template in INSIGHT_THEME_TEMPLATES:
        if kw_set & theme_kws:
            return template
    return DEFAULT_INSIGHT


def fallback_summary(item):
    """ANTHROPIC_API_KEY가 없을 때: 규칙 기반으로 핵심내용/인사이트 생성
    (대시보드에서 불릿+볼드로 렌더링되도록 마크다운 형식(-, **) 사용)"""
    core = item["description"] or item["title"]
    if len(core) > 140:
        core = core[:140].rstrip() + "..."

    kws = matched_keywords(f"{item['title']} {item['description']}")
    # 본문에 등장하는 핵심 키워드를 볼드 처리
    bolded_core = core
    for kw in kws[:3]:
        if kw in bolded_core:
            bolded_core = bolded_core.replace(kw, f"**{kw}**")
    core_md = f"- {bolded_core}"

    # 매칭된 키워드의 주제(관세·통상 / 지정학 / 대관·외교 / 정책)에 맞는 인사이트를 골라서
    # 이 기사에 실제로 등장한 내용에 조금 더 엮이도록 구성 (모든 기사에 똑같은 문장 X)
    insight = f"- {build_fallback_insight(kws)}"
    return core_md, insight


def fallback_category_summary(category: str, items: list, overall: bool = False) -> str:
    """ANTHROPIC_API_KEY가 없을 때: 카테고리별(또는 전체) 규칙 기반 종합 요약
    (LLM 없이도 최대한 "총괄 요약"에 가깝게 — 키워드 빈도 + 대표 기사 제목 몇 개를
    불릿+볼드 마크다운 형식으로 구성. 대시보드가 이 형식을 파싱해서 불릿 리스트로
    보여줍니다. 진짜 자연어 종합은 ANTHROPIC_API_KEY를 등록해야 Claude가 생성하며,
    이건 그게 없을 때의 대체용입니다.)"""
    if not items:
        return f"- 오늘 '{category}' 카테고리에 해당하는 기사가 없습니다."

    kw_counts = {}
    for it in items:
        for kw in matched_keywords(f"{it['title']} {it['core']}"):
            kw_counts[kw] = kw_counts.get(kw, 0) + 1
    top_kws = sorted(kw_counts, key=lambda k: -kw_counts[k])[:5]
    kw_str = ", ".join(f"**{k}**" for k in top_kws) if top_kws else "**대외협력 전반**"

    # 대표 기사 제목 (최신순 상위 3개) — "몇 건 수집됨" 수준이 아니라
    # 실제 헤드라인을 불릿으로 보여줘서 무슨 내용인지 바로 감이 오도록 구성
    headline_titles = [it["title"] for it in items[:3]]

    lines = []
    if overall:
        cat_counts = {}
        for it in items:
            cat_counts[it["category"]] = cat_counts.get(it["category"], 0) + 1
        breakdown = ", ".join(f"**{c}** {n}건" for c, n in cat_counts.items())
        lines.append(f"오늘은 총 **{len(items)}건**의 기사가 수집되었습니다 ({breakdown})")
    else:
        lines.append(f"오늘 '{category}' 카테고리에는 총 **{len(items)}건**의 기사가 수집되었습니다")
    lines.append(f"주요 키워드: {kw_str}")
    for t in headline_titles:
        lines.append(f"대표 기사: **{t}**")
    lines.append(
        "더 자연스러운 종합 요약을 원하시면 ANTHROPIC_API_KEY를 GitHub Secrets에 "
        "등록하시면 Claude가 직접 요약을 작성해드려요."
    )
    return "\n".join(f"- {l}" for l in lines)


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
요약해주세요 (개별 기사를 하나씩 재서술하지 말고, "오늘 이 카테고리 전체를 보면 ~한 흐름이다"는
종합적 시각으로). 가능하면 위 JD/자소서 맥락과 연결되는 시사점도 포함하세요.

형식 지침 (한눈에 읽히도록 마크다운으로 작성):
- 한 카테고리당 2~4개의 불릿포인트로 작성하세요. 각 불릿은 새 줄에 "- "로 시작합니다.
- 각 불릿에서 가장 핵심적인 키워드나 문장은 **이렇게 볼드**로 표시하세요.
- 불릿 안 문장은 1문장 이내로 간결하게 쓰세요.

반드시 아래 JSON 객체 형식으로만 답하세요 (각 값은 "- ...\\n- ..." 형태의 멀티라인 문자열).
카테고리 이름은 위 목록의 대괄호 안 이름과 정확히 동일하게 쓰고, 목록에 없는 카테고리는
포함하지 마세요. 다른 설명은 절대 붙이지 마세요:
{{"현대자동차": "- 첫 번째 불릿\\n- 두 번째 불릿", "자동차 산업": "- ...", "국제 정세": "- ..."}}
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
"핵심내용"(기사의 사실관계 요약)과 "면접 인사이트"(이 기사를 면접에서 어떻게 활용할 수
있는지 JD/자소서와 연결)를 작성해주세요.

형식 지침 (한눈에 읽히도록 마크다운으로 작성):
- 핵심내용과 면접 인사이트 각각 1~2개의 불릿포인트로 작성하세요. 각 불릿은 새 줄에
  "- "로 시작합니다 (여러 개면 "\\n"으로 구분).
- 각 불릿에서 가장 핵심적인 키워드나 문장은 **이렇게 볼드**로 표시하세요.
- 불릿 하나는 1문장 이내로 간결하게 쓰세요.

{articles_block}

---

반드시 아래 JSON 배열 형식으로만 답하세요 (core, insight 값은 "- ...\\n- ..." 형태의
멀티라인 문자열). 다른 설명은 절대 붙이지 마세요:
[
  {{"index": 1, "core": "- 핵심 불릿1\\n- 핵심 불릿2", "insight": "- 인사이트 불릿1"}},
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
            "source": it.get("source", ""),
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
