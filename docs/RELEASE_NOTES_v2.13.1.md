# PRISM-INSIGHT v2.13.1 — Open-Issue Triage Patch

> **Release Date**: 2026-05-25
> **PRs**: #1 (HitL retry handler) → main `576b47a`, #2 (open-issue triage) → main `276c57d`

## 개요

v2.13.0 본체 머지 직후 백로그에 누적되어 있던 오픈 이슈 5건 중 4건을 해결하고 1건은 부분 해결(첫 audit 단계)했습니다. 함께 들어간 v2.13.0 본체의 **`/retry_<승인ID> <금액>`** MessageHandler 누락 보완도 v2.13.0 자체 노트에는 반영되어 있지만, 후속으로 들어간 fix들이 같은 릴리스 사이클이라 본 패치 노트로 묶어 추적성을 확보합니다.

기능 추가 없음. 모두 명확한 버그/UX fix + prompt-regression 테스트. 코드 회귀 위험 낮음 (KR/US 매수 메시지 및 보고서 문구 변경).

## 주요 변경사항

### 1. `/retry_<승인ID> <금액>` MessageHandler 구현 (PR #1)

v2.13.0 본체에서 📝 금액 수정 버튼 클릭 시 `MODIFY_REQUESTED` 기록 후 `"새 금액으로 /retry_<short_id> 를 입력해주세요"` 안내가 나갔지만 실제 MessageHandler가 없어 dead-end였습니다.

- `ApprovalManager._modify_pending` — 인메모리 stash가 원본 `TradeProposal`(metadata 포함)을 보관
- `ApprovalManager.retry_with_amount(short_id, new_amount_krw, bot, chat_id)` — stash 조회 → 새 `approval_id`로 fresh 카드 발급
- 쉼표 허용(`300,000`), 금액 누락/0/음수/만료 ID 등에 대해 명확한 응답
- `approval/handler.py` `modify_message_template`의 placeholder 버그 동시 수정 (`<short_id>` 리터럴 → `{short_id}` 포맷)

세부는 [v2.13.0 노트](RELEASE_NOTES_v2.13.0.md) 참고.

### 2. 당일 매도 후 매수 차단 (#282)

`is_ticker_in_holdings`가 현 포트폴리오(`stock_holdings`)만 체크해서, 매도 후 row가 `trading_history`로 이동하면 같은 날 AI 매수 신호가 가드를 통과해 방금 청산한 포지션을 재진입하던 문제.

- 신규 `tracking.helpers.was_sold_today(cursor, ticker, account_key)` 및 US `prism-us/tracking/db_schema.was_us_ticker_sold_today` — `trading_history`/`us_trading_history`에서 `substr(sell_date,1,10) = today` 조회
- 계정별 scoping — 계좌 A 매도가 계좌 B 매수를 막지 않음
- `stock_tracking_agent.buy_stock` + `prism-us/us_stock_tracking_agent.buy_stock` 양쪽에서 `_is_ticker_in_holdings` 직후 가드 호출
- 6 unit test: 당일 positive, 어제 negative, 계좌간 격리, account 미지정 global, US 동작 미러, 테이블 부재 fail-open

### 3. `/ask` 도구 호출 날짜 가드 (#283)

Spark agent가 "최신 정보를 기반으로" 지시문만으로는 1년 전 데이터 범위로 도구를 호출하던 문제. 프롬프트 재구성:

- ISO 날짜 + 한국어 날짜 + 연도 문자열 모두 명시
- 도구 호출 시 `start_date >= today - 30d`, `end_date <= today` 강제
- 검색 쿼리에 연도 명시 의무화
- 1년 이상 지난 자료만 반환되면 재시도 또는 "최근 자료가 부족함" 명시
- 의도적 historical 인용 시 `'202X년 데이터입니다'` 명시 의무

코드 경로 변경 없음 — 프롬프트만 수정.

### 4. `2.1 기업현황` 섹션 firecrawl 결과 누락 방어 (#286)

봇(매 요청 fresh subprocess)은 정상이나 배치(같은 프로세스에서 종목별 MCPApp 반복 생성/소멸)에서 firecrawl scrape이 간헐 누락. 침묵 실패를 **관찰 가능 실패**로 전환:

- `cores/agents/company_info_agents.py` (KR + EN) 지침에 4-rule 복원력 블록 추가:
  1. `firecrawl_scrape`가 빈 본문/오류/500자 미만 → 같은 URL로 1회 재시도
  2. 재시도 실패 시 `firecrawl_search` fallback
  3. 침묵 누락 금지 — 실패 필드는 `데이터 미수집 (도구 호출 실패)` 명시
  4. 보고서 마지막에 `<!-- firecrawl_status: ok | partial | failed -->` 마커 (운영자 grep용)
- `mcp_agent.config.yaml.example` firecrawl 항목에 `read_timeout_seconds: 120` 추가 (webresearch와 동일 기준)
- 5 prompt-regression test (CI-friendly, mcp_agent import 없이 소스 텍스트만 검증)

### 5. AI bullish + 가드 차단 시 명시적 콜아웃 (#281)

삼성전기 사례 — AI는 `Score 7/10`, `결정: Enter`였지만 `MAX_SAME_SECTOR=3`(3번째 전기·전자)로 차단. 메시지 헤더가 `⚠️ 매수 보류`라 "AI가 거부한 것"으로 오해됨.

새 메시지:

```
⚠️ 매수 보류: 삼성전기(009150)
현재가: 900,000원
매수 Score: 7/10
결정: Enter
시장 상황: 강한 강세장
산업군: 전기·전자
보류 사유: 섹터 집중 (전기·전자)

💡 AI는 매수를 추천했지만 포트폴리오 가드로 자동 매매가 보류되었습니다.
   (사유: 섹터 집중 (전기·전자)) 필요 시 직접 검토 후 수동 매수를 고려하실 수 있습니다.

분석 의견: ...
```

- KR (`stock_tracking_enhanced_agent.py`): `decision == "Enter" AND buy_score >= min_score AND not sector_diverse`
- US (`prism-us/us_stock_tracking_agent.py`): `scenario.decision == "entry" AND buy_score >= min_score AND skip_reason contains "sector concentration"`
- 4 prompt-regression test (head/guide 텍스트, 3-condition predicate, 위치 제약)

### 6. 매매일지 피드백루프 투명화 첫 단계 (#280 부분 해결)

이슈 발의자(프리즘 본인)도 "추후 개선"이라 답한 큰 작업 — 본 패치는 **첫 audit 단계**입니다.

`tracking/journal.py`:
- `get_provenance_for_ticker(ticker, ...)` — 기존 `get_context_for_ticker`(프롬프트 텍스트)의 자매 함수. 같은 SQL 필터로 매수에 영향을 줄 artifact의 **개수 + 원본 ID**만 반환
- `format_provenance_one_liner(provenance)` — Telegram 친화 1-line 요약

`stock_tracking_agent.buy_stock()`:
- 매수 시 provenance 조회 → 원본 ID들을 INFO 로그 (audit 추적용)
- 매수 메시지에 1-line 추가:

```
📚 매매일지 참조: 누적 원칙 5개 · 같은 종목 일지 2개 · 직관 7개
```

6 unit test (in-memory sqlite mirror).

**남은 작업** (별도 PR/세션):
- (a) provenance ID들을 `stock_holdings`에 영속화 — 봇 재시작 후에도 audit 추적
- (b) 매도 후 평가에서 각 원칙별 기여도 점수화
- (c) 대시보드 뷰: "원칙 X가 N개 매매에 영향 → 평균 수익률 Y%"

## 변경된 주요 파일

| 파일 | PR | 변경 |
|------|----|------|
| `approval/handler.py` | #1 | `_modify_pending` stash, `retry_with_amount`, 메시지 템플릿 fix |
| `trading/approval_integration.py` | #1 | `telegram_retry_handler` |
| `telegram_ai_bot.py` | #1 | `MessageHandler(filters.Regex(r'^/retry_[0-9a-fA-F]'))` + `/ask` 프롬프트 강화 |
| `tracking/helpers.py`, `tracking/__init__.py` | #1 | `was_sold_today` |
| `stock_tracking_agent.py` | #1, #2 | same-day 가드, 매수 메시지에 provenance line |
| `prism-us/tracking/db_schema.py` | #1 | `was_us_ticker_sold_today` |
| `prism-us/us_stock_tracking_agent.py` | #1, #2 | same-day 가드, AI bullish 콜아웃 |
| `stock_tracking_enhanced_agent.py` | #2 | AI bullish 콜아웃 |
| `cores/agents/company_info_agents.py` | #2 | 4-rule firecrawl 복원력 블록 |
| `mcp_agent.config.yaml.example` | #2 | firecrawl `read_timeout_seconds: 120` |
| `tracking/journal.py` | #2 | `get_provenance_for_ticker`, `format_provenance_one_liner` |
| `tests/test_*` 신규 4종 | #1, #2 | `same_day_rebuy_guard`, `company_status_agent_resilience`, `ai_bullish_guard_callout`, `journal_provenance` |
| `.gitignore` | #1 | `.coverage`, `.claude/`, `trade_approvals.db` 등 |

## 마이그레이션

스키마 변경 없음, env 변경 없음, requirements 변경 없음. 재시작만 하면 됩니다.

운영자 권장 점검:
- 익일 morning 배치 PDF에 `<!-- firecrawl_status: -->` 마커가 보이는지 확인 (#286)
- 섹터 집중으로 차단되는 매수 보류 메시지에 💡 콜아웃이 붙는지 확인 (#281)
- 매수 메시지에 `📚 매매일지 참조` 라인이 보이는지 확인 (#280)

## 알려진 제한사항

1. **#280 영속화 없음**: provenance는 INFO 로그 + 메시지에만 남음. 봇 재시작 후 과거 매수의 referenced IDs를 SQL로 조회할 수 없음. 별도 PR에서 `stock_holdings`에 컬럼 추가 필요.
2. **#286 근본 원인은 미해결**: firecrawl MCP 재기동 race condition 자체는 그대로. 본 패치는 발생 시 추적 가능하게 만들기만 함. 장기적으로는 MCPApp 라이프사이클 재설계 필요.
3. **#283 검증 불가**: Spark agent 동작이 외부 LLM에 종속되므로 단위 테스트 불가. 사용자 보고 기반 회귀 모니터링 필요.
4. **prompt-regression test 한계**: 텍스트 문자열 매칭이므로 의미는 동일하되 문구가 바뀌면 false positive 발생. 변경 시 테스트도 함께 업데이트.

## 텔레그램 공지

### 한국어

```
🛠️ PRISM-INSIGHT v2.13.1 — 오픈 이슈 5건 트리아지

v2.13.0 머지 직후 백로그에 있던 사용자 보고 이슈들을 처리했습니다:

✅ #282 — 매도 직후 같은 종목 재매수 차단 (수수료 낭비 방지)
✅ #283 — /ask 명령이 1년 전 데이터를 끌어오던 문제 해결
✅ #286 — 보고서 "2.1 기업현황" 섹션 firecrawl 누락을 관찰 가능하게
✅ #281 — AI는 매수 추천인데 가드로 차단된 경우 명시적 안내 추가
🔧 #280 — 매매일지 피드백루프 1단계 투명화 (provenance audit 로깅)

📚 매수 메시지에 "누적 원칙 N개 · 같은 종목 일지 M개 · 직관 K개"
참조 정보가 1줄로 추가됩니다. 매매일지가 어떻게 의사결정에
반영되는지 보이도록 한 첫 단계입니다.

⚙️ DB 마이그레이션·env 변경 없음. 재시작만 하면 적용됩니다.
```

### English

```
🛠️ PRISM-INSIGHT v2.13.1 — Open-Issue Triage Patch

Five issues from the post-v2.13.0 backlog:

✅ #282 — block same-day re-buy after sell (KR + US, per-account)
✅ #283 — /ask now anchors tool-call date ranges to today-30d
✅ #286 — firecrawl flakiness in 2.1 corp-status section is now
   observable (retry → fallback → status marker)
✅ #281 — explicit callout when AI was bullish but only the
   portfolio guard (sector / slots) blocked auto-execution
🔧 #280 — first audit step: buy messages now show
   "누적 원칙 N개 · 같은 종목 일지 M개 · 직관 K개" so the journal
   feedback loop is visible. Full persistence is a follow-up.

⚙️ No DB migration, no env changes. Restart only.
```

---

**Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>**
