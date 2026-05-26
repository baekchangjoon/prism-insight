# 런북 — 시나리오 5: KIS 모의투자 자동매매 (DEMO)

> **대상**: 시나리오 4를 마치고 매매까지 자동화하고 싶지만 실거래 위험은 피하고 싶은 사용자
> **위험도**: 낮음 (KIS 모의투자 계좌 — 실제 돈 X)
> **소요 시간**: 첫 설정 30분, 운영 시작 후 사람이 개입할 일 거의 없음
> **선행**: [RUNBOOK_SCENARIO_4_LOCAL_ANALYSIS.md](RUNBOOK_SCENARIO_4_LOCAL_ANALYSIS.md) 완료

## 1. 사전 요구사항

### 시나리오 4에 추가
- **KIS 개발자포털 가입** + **모의투자 계좌 발급** (무료, https://apiportal.koreainvestment.com)
  - 신청 → app_key + app_secret 발급
  - 모의투자 계좌번호: `XXXXXXXX-XX` 형식 (앞 8자리 CANO + 뒤 2자리 상품코드)
- 시나리오 4 분석이 정상 동작 확인됨 (`reports/`에 PDF가 생성되는 상태)

### KIS 자료 (참고)
- 공식 가이드: https://apiportal.koreainvestment.com/apiservice-summary
- 샘플 코드: https://github.com/koreainvestment/open-trading-api

## 2. KIS 설정 파일

```bash
cp trading/config/kis_devlp.yaml.example trading/config/kis_devlp.yaml
```

**`trading/config/kis_devlp.yaml`** 핵심 필드:

```yaml
default_unit_amount: 500000      # 종목당 매수 금액 (KRW). 모의 시작 시 작게 추천
default_unit_amount_usd: 500     # US 종목당 매수 금액 (USD)
auto_trading: true
default_mode: demo               # ⭐ 반드시 demo (실거래 전환은 시나리오 6에서)
default_product_code: "01"       # 일반 주식 계좌

# 단일 계좌 (legacy)
my_app: ""                       # 비워두면 accounts: 사용
my_sec: ""
my_acct_stock: ""

# 모의투자 도메인 (수정 금지)
prod: https://openapi.koreainvestment.com:9443           # 실전 — demo에서는 미사용
vps:  https://openapivts.koreainvestment.com:29443       # ⭐ 모의투자

# 다계좌 지원 (v2.9.0+)
accounts:
  - id: primary                  # 첫 번째 = primary = Telegram 보고서 발신 계정
    mode: demo                   # demo / real
    app_key: "PSxxx..."          # 모의투자용 app_key
    app_secret: "xxx..."         # 모의투자용 app_secret
    account: "50000000"          # CANO (앞 8자리)
    product: "01"                # ACNT_PRDT_CD (뒤 2자리)
    market: "all"                # kr / us / all
    primary: true
    buy_amount_krw: 500000       # 이 계좌 매수액 (선택, 없으면 default_unit_amount)
```

> **권한 분리**: 시나리오 5는 `mode: demo` 계좌만 등록하세요. `accounts:`에 `mode: real`이 섞여 있으면 시나리오 6 운영이 됩니다.

## 3. 사전 연결 점검

```bash
# 3.1 KIS 토큰 발급 확인 (KIS API에 도달 가능한지)
python tests/test_kis_auth_mock.py

# 3.2 모의 계좌 잔고 조회 (네트워크/자격증명 검증)
KIS_ENV=real python -c "
from trading import kis_auth as ka
ka.set_active_account(ka.getEnv()['accounts'][0])
print(ka.get_account_balance())
"
# → 예수금 + 보유종목 dict가 보이면 정상
```

> KIS 모의투자 계좌 발급 직후 잔고는 보통 1,000만원 (한투 정책).

## 4. 첫 매매 — 수동 단발 테스트

```bash
# 4.1 단일 종목 분석 (시나리오 4와 동일)
python demo.py 005930

# 4.2 분석 결과 기반 매매 판단 + 주문 (모의)
python stock_tracking_agent.py
```

**기대 동작**:
1. `reports/` 디렉토리의 최신 보고서 스캔
2. 보고서별로 Buy Specialist 에이전트가 매수 점수·근거·시나리오 산출
3. 매수 조건 충족 (Score ≥ min_score, 결정=Enter, 섹터/슬롯 가드 통과) → 모의 매수 주문
4. `stock_holdings` 테이블에 row 추가, 매수 메시지 Telegram 전송

**검증**:
```bash
sqlite3 stock_tracking_db "SELECT ticker, company_name, buy_price, buy_date FROM stock_holdings;"
sqlite3 stock_tracking_db "SELECT ticker, buy_score, decision FROM analysis_performance_tracker ORDER BY created_at DESC LIMIT 5;"
```

Telegram 메시지에 `📚 매매일지 참조: ...` 라인 (v2.13.1)이 보이면 피드백루프 정상 작동.

## 5. 자동 매매 루프 시작

### 5.1 일일 분석 + 매수 (장 시작 전)

```cron
# crontab -e
# 평일 07:00 — 급등 후보 추출
0 7 * * 1-5  cd /path/to/prism-insight && python3 trigger_batch.py morning INFO >> logs/trigger.log 2>&1

# 평일 08:00 — 후보별 분석 보고서 생성 + Telegram 전송
0 8 * * 1-5  cd /path/to/prism-insight && python3 stock_analysis_orchestrator.py --mode morning >> logs/morning.log 2>&1

# 평일 08:50 — 매수 판단 + 모의 주문
50 8 * * 1-5  cd /path/to/prism-insight && python3 stock_tracking_agent.py >> logs/tracking_buy.log 2>&1
```

### 5.2 장중 매도 판단 (5-10분 주기)

```cron
# 평일 09:00~15:30 (5분 주기) — 보유 종목 가격 갱신 + 매도 판단
*/5 9-15 * * 1-5  cd /path/to/prism-insight && python3 stock_tracking_agent.py >> logs/tracking.log 2>&1
```

> Sell Specialist는 보유 종목별로 다음 조건 평가:
> - 손절 라인 도달 (trigger별 -5% ~ -7%)
> - 목표가 도달
> - 추세 약화 (RSI·볼린저·거래량 종합)
> - 보유 기간 초과
> - distribution day kill switch (v2.12.0)

### 5.3 주간 정리 (선택)

```cron
# 일요일 03:00 — 매매 일지 압축 + GC
0 3 * * 0  cd /path/to/prism-insight && python3 compress_trading_memory.py >> logs/compress.log 2>&1
```

## 6. 기본 매매 제약 (코드 디폴트)

| 항목 | 값 | 위치 |
|------|---|------|
| 최대 보유 종목 | 10 | `stock_tracking_agent.py:MAX_SLOTS` |
| 같은 섹터 최대 | 3 | `stock_tracking_agent.py:MAX_SAME_SECTOR` |
| 기본 매수액 | `default_unit_amount` (KRW) | `kis_devlp.yaml` |
| 손절 (intraday_surge) | -5% | `cores/agents/trading_agents.py` |
| 손절 (volume_surge) | -7% | 동일 |
| 시장 체제별 min_score | 4-6 | v2.12.0 regime matrix |
| 같은 날 매도 후 재매수 | 차단 | v2.13.1 (`was_sold_today`) |

매매 전략 커스터마이즈는 [CLAUDE_TASKS.md Task 4](CLAUDE_TASKS.md#task-4-modifying-trading-strategy).

## 7. 매매 일지 활성화 (권장)

```bash
# .env에 추가
echo "ENABLE_TRADING_JOURNAL=true" >> .env
```

활성화 시:
- 매도 후 AI가 자동으로 매매 일지 작성 (단/중/장기 기억)
- 다음 매수 시 누적 원칙·이전 거래 일지가 LLM 컨텍스트에 자동 주입 (소극적이지만 작동)
- v2.13.1 부분 fix로 매수 메시지에 `📚 매매일지 참조: 누적 원칙 N개...` 1-line 표시
- INFO 로그에 `Journal provenance for buy {ticker}: principles=[...] same_stock_journals=[...] intuitions=[...]` 기록

자세한 사용법: [TRADING_JOURNAL.md](TRADING_JOURNAL.md).

## 8. 검증 체크리스트 (운영 첫 주)

```bash
# 8.1 매수 시도가 매일 일어나는지
sqlite3 stock_tracking_db "
SELECT date(buy_date) AS day, COUNT(*) AS buys
FROM stock_holdings
GROUP BY day ORDER BY day DESC LIMIT 7;
"

# 8.2 매도 시도와 수익률
sqlite3 stock_tracking_db "
SELECT date(sell_date) AS day, COUNT(*) AS sells, ROUND(AVG(profit_rate),2) AS avg_pnl
FROM trading_history
GROUP BY day ORDER BY day DESC LIMIT 7;
"

# 8.3 매수 거부 사유 분포 (#281이 정상 동작하는지)
sqlite3 stock_tracking_db "
SELECT skip_reason, COUNT(*) FROM analysis_performance_tracker
WHERE was_traded = 0 GROUP BY skip_reason ORDER BY 2 DESC LIMIT 10;
"

# 8.4 firecrawl 누락 (#286 모니터링)
grep -h "firecrawl_status:" reports/*_$(date +%Y%m%d)*.md | sort | uniq -c
```

**1주차 기대 패턴**:
- 매수 1-5건/일 (시장 상황 따라)
- 매도는 보유 종목 발생 후
- skip_reason은 `점수 부족`/`섹터 집중`이 다수 — 정상

## 9. Telegram 알림 패턴

매수 성공 시 알림 예시:
```
📈 신규 매수: 삼성전자(005930)
매수가: 70,000원
목표가: 78,000원
손절가: 66,500원
투자기간: 단기
산업군: 전기·전자
거래대금 분석: 전일 대비 +15%
투자근거: ...
📚 매매일지 참조: 누적 원칙 5개 · 같은 종목 일지 2개 · 직관 7개
```

매수 보류 시 알림 예시:
```
⚠️ 매수 보류: 삼성전기(009150)
현재가: 900,000원
매수 Score: 7/10
결정: Enter
산업군: 전기·전자
보류 사유: 섹터 집중 (전기·전자)

💡 AI는 매수를 추천했지만 포트폴리오 가드로 자동 매매가 보류되었습니다.
   필요 시 직접 검토 후 수동 매수를 고려하실 수 있습니다.
```

매도 시 알림:
```
📉 매도 완료: 삼성전자(005930)
매수가 → 매도가: 70,000원 → 75,000원
수익률: +7.14%
보유 기간: 5일
매도 사유: 목표가 도달
```

## 10. 외부 신호 구독 (선택)

타사/본인의 시그널을 Redis/GCP Pub/Sub로 받아서 자동 매매:

```bash
# .env
UPSTASH_REDIS_REST_URL="https://xxx.upstash.io"
UPSTASH_REDIS_REST_TOKEN="..."
# 또는
GCP_PROJECT_ID="..."
GCP_PUBSUB_SUBSCRIPTION_ID="..."
GCP_CREDENTIALS_PATH="/path/to/sa.json"

# Redis 구독자
python examples/messaging/redis_subscriber_example.py --dry-run    # 신호만 보기

# GCP Pub/Sub 구독자
python examples/messaging/gcp_pubsub_subscriber_example.py --polling-interval 60 --dry-run
```

자세한 옵션: [CLAUDE_TASKS.md Task 7](CLAUDE_TASKS.md#task-7-event-driven-trading-signal-integration).

## 11. 모니터링 / 알람

```bash
# 매일 자동 점검 cron 예시 (저녁 8시)
0 20 * * *  cd /path/to/prism-insight && python3 -c "
import sqlite3
c = sqlite3.connect('stock_tracking_db')
today = c.execute('SELECT COUNT(*) FROM stock_holdings WHERE date(buy_date)=date(\"now\")').fetchone()[0]
sells = c.execute('SELECT COUNT(*) FROM trading_history WHERE date(sell_date)=date(\"now\")').fetchone()[0]
print(f'Today: buys={today}, sells={sells}')
" >> logs/daily_summary.log
```

자체 대시보드: [examples/dashboard/](../examples/dashboard/)
- `python examples/generate_dashboard_json.py`로 JSON 생성
- 그 다음 React 빌드해서 정적 서빙

## 12. 트러블슈팅

| 증상 | 원인 / 해결 |
|------|-----------|
| KIS 토큰 발급 실패 | `kis_devlp.yaml`의 app_key/app_secret 검증, 모의투자 키는 vps 도메인이어야 함 |
| `CREDENTIAL MISMATCH! Using REAL app key (PS*) in DEMO mode` | v2.14.1+: paper key가 `PSVT*` 접두사가 아니어도 KIS demo 도메인은 정상 수락하는 경우 있음. `PRISM_KIS_BYPASS_PREFIX_CHECK=true` 환경변수로 우회 가능 (실제 키가 KIS demo에 등록돼 있을 때만 사용) |
| `EGW00201 초당 거래건수를 초과하였습니다` | KIS 시세 API의 per-second rate limit. v2.14.1+에서 `get_current_price()`가 자동으로 1회 backoff 후 재시도 |
| `APBK1234` / `APTR0057` KIS 오류 | v2.9.0에서 fix됨 — git pull |
| 매수 시 "이미 보유" 오류 | `stock_holdings`에 row 존재 — `WHERE ticker = ?` 수동 확인 |
| 매도 시 "수량 부족" | KIS 모의계좌 동기화 지연 — 1-2분 후 재시도 (자동 재시도 있음) |
| 같은 종목 반복 매수 | v2.13.1 fix — `was_sold_today` 가드 — git pull |
| `📚 매매일지 참조` 안 보임 | `ENABLE_TRADING_JOURNAL=true` 확인 + journal DB row 존재 여부 |
| 매수 메시지 영문 regime | v2.13.0 fix — git pull |
| 1분당 1회 토큰 발급 제한 (EGW00133) | KIS 정책 — 토큰은 `trading/config/KIS<date>` 또는 `KIS_acct_<hash>.token`에 자동 캐싱되므로 정상 운영 시 발생 안 함. 캐시 파일 수동 삭제 후 재시도 시 발생 가능 |

## 13. 데이터 위치

| 위치 | 내용 |
|------|------|
| `stock_tracking_db` (SQLite) | 매매 핵심 데이터 |
| `stock_holdings` 테이블 | 현재 포트폴리오 |
| `trading_history` 테이블 | 매매 이력 + 수익률 |
| `trading_journal` 테이블 | AI 자동 작성 매매 일지 (`ENABLE_TRADING_JOURNAL=true`일 때) |
| `trading_principles` 테이블 | 누적 원칙 (다음 매수 컨텍스트에 자동 주입) |
| `analysis_performance_tracker` 테이블 | 분석된 종목의 7/14/30일 사후 성과 |
| `watchlist_history` 테이블 | 분석했지만 매수 안 한 종목 |
| `trading/config/KIS<YYYYMMDD>` | KIS 토큰 캐시 (자동 갱신) |
| `archive.db` (SQLite) | `/insight` 명령용 누적 분석 아카이브 (v2.10.0) |

## 14. 비활성화 / 일시중지

```bash
# cron 라인 주석 처리
crontab -e

# 또는 .env에서
echo "auto_trading: false" >> trading/config/kis_devlp.yaml
```

> SQLite는 그대로 두면 다음 활성화 시 이어서 작동. 완전 초기화 원할 때만 `rm stock_tracking_db`.

## 15. 다음 단계

- **실거래 + 사람 확정**으로 전환하려면 → [RUNBOOK_SCENARIO_6_HITL_REAL.md](RUNBOOK_SCENARIO_6_HITL_REAL.md)
- 매매 전략 튜닝 → [CLAUDE_TASKS.md Task 4](CLAUDE_TASKS.md#task-4-modifying-trading-strategy)
- 새 AI 에이전트 추가 → [CLAUDE_TASKS.md Task 1](CLAUDE_TASKS.md#task-1-adding-a-new-ai-agent)

---

**관련**: [RUNBOOK_SCENARIO_4_LOCAL_ANALYSIS.md](RUNBOOK_SCENARIO_4_LOCAL_ANALYSIS.md) | [TRADING_JOURNAL.md](TRADING_JOURNAL.md) | [CLAUDE.md](../CLAUDE.md)
