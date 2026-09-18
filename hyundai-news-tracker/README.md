# 현대차 대외협력 뉴스 트래커

현대자동차 대외협력 직무 면접 준비를 위해, 관세·통상·지정학·정책·대관 관련 뉴스를
매일 자동으로 수집·요약하고 GitHub Pages 대시보드에서 날짜별로 확인할 수 있게 해주는
저장소입니다.

- 데이터 수집: [네이버 뉴스 검색 API](https://developers.naver.com)
- 자동 실행: GitHub Actions (매일 한국시간 오전 9시)
- 대시보드: GitHub Pages (`/docs` 폴더, 날짜 선택 드롭다운 포함)
- 과거 기록: 삭제되지 않고 `docs/data/YYYY-MM-DD.json`으로 계속 누적
- 중복 방지: 한 번 사용된 기사 링크는 `docs/data/seen_links.json`에 기록되어 영구 제외

## 폴더 구조

```
.
├── .github/workflows/daily-news.yml   # 매일 실행되는 GitHub Actions 워크플로
├── requirements.txt                    # Python 의존성
├── scripts/
│   ├── fetch_and_summarize.py          # 뉴스 수집 + 요약 스크립트
│   └── context.txt                     # JD + 자소서 컨텍스트 (선별/요약 기준)
└── docs/                               # GitHub Pages로 배포되는 폴더
    ├── index.html                      # 대시보드 (날짜 선택 가능)
    └── data/
        ├── index.json                  # 저장된 날짜 목록
        ├── seen_links.json             # 이미 사용한 기사 링크 (중복 방지)
        └── YYYY-MM-DD.json             # 날짜별 10개 기사 요약
```

이미 2026-09-18 데이터가 예시로 들어있습니다 (앞선 대화에서 수동으로 조사한 10개 기사).
Actions가 처음 실행되면 그 다음 날짜부터 자동으로 쌓입니다.

## 설정 방법 (Step by step)

### 1. GitHub 저장소 생성

1. GitHub에서 새 저장소를 만듭니다 (Public이어야 GitHub Pages 무료 사용 가능. Private을 쓰려면 GitHub Pro 이상 필요).
2. 이 폴더(`hyundai-news-tracker/`) 안의 파일들을 그대로 저장소에 올립니다 (git init → add → commit → push, 또는 GitHub 웹에서 파일 업로드).

```bash
cd hyundai-news-tracker
git init
git add .
git commit -m "initial commit"
git branch -M main
git remote add origin https://github.com/<본인계정>/<저장소이름>.git
git push -u origin main
```

### 2. API 키를 GitHub Secrets에 등록

저장소 페이지에서 **Settings → Secrets and variables → Actions → New repository secret**으로 이동해서 아래 두 개를 등록합니다 (절대 코드에 직접 쓰지 않기):

| Name | Value |
|---|---|
| `NAVER_CLIENT_ID` | 네이버 개발자센터에서 발급받은 Client ID |
| `NAVER_CLIENT_SECRET` | 네이버 개발자센터에서 발급받은 Client Secret |

**(선택)** Claude API로 "핵심내용/면접 인사이트"까지 AI가 자동 생성하게 하려면, [console.anthropic.com](https://console.anthropic.com)에서 API 키를 발급받아 `ANTHROPIC_API_KEY`라는 이름으로 같은 방식으로 등록하세요. 등록하지 않으면 스크립트가 자동으로 네이버가 주는 기사 설명(description)과 규칙 기반 문장으로 대체합니다 — 완전히 무료로 동작합니다.

### 3. GitHub Pages 활성화

저장소 **Settings → Pages**로 이동해서:
- Source: **Deploy from a branch**
- Branch: **main**, 폴더: **/docs**
- Save

몇 분 후 `https://<본인계정>.github.io/<저장소이름>/` 주소로 대시보드가 열립니다.

### 4. 워크플로 수동 실행으로 테스트

저장소의 **Actions** 탭 → **Daily Hyundai News Digest** → **Run workflow** 버튼으로 바로 실행해볼 수 있습니다.
성공하면 `docs/data/`에 오늘 날짜의 새 JSON 파일이 커밋되고, 대시보드에 자동으로 반영됩니다.
(cron은 매일 UTC 0시 = 한국시간 오전 9시에 자동 실행되지만, 등록 직후 첫 실행은 수동으로 한 번 눌러 확인하는 걸 권장합니다.)

## 커스터마이징

- **검색 키워드 바꾸기**: `scripts/fetch_and_summarize.py`의 `QUERIES` 리스트 수정
- **관련도 점수 가중치 바꾸기**: 같은 파일의 `RELEVANCE_KEYWORDS` 딕셔너리 수정
- **하루에 뽑는 기사 개수 바꾸기**: `TOP_N` 값 수정 (기본 10)
- **신선도 기준(며칠 이내 기사만) 바꾸기**: `FRESH_DAYS` 값 수정 (기본 5일)
- **선별/요약 기준 자체를 바꾸기**: `scripts/context.txt`를 수정하면, Claude API 요약을 쓸 경우 그 내용이 그대로 프롬프트에 반영됩니다.

## 주의사항

- 네이버 뉴스 검색 API는 각 언론사 기사에 대한 **메타데이터(제목/링크/요약)** 만 제공합니다. 기사 본문 전체를 가져오는 것이 아니므로, Claude API 없이 쓰는 규칙 기반 "핵심내용"은 네이버가 제공하는 짧은 description을 그대로 쓰는 수준입니다 — 더 정교한 요약을 원하면 `ANTHROPIC_API_KEY`를 등록하세요.
- 네이버 뉴스 검색 API는 하루 호출 한도가 있습니다 (기본 25,000회/일 수준으로 넉넉함). 쿼리 개수(`QUERIES`)를 크게 늘리지 않는 한 문제 없습니다.
- `docs/data/seen_links.json`에 있는 링크는 앞으로 다시 추천되지 않습니다. 특정 기사를 다시 보고 싶다면 이 파일에서 해당 링크를 지우면 됩니다.
