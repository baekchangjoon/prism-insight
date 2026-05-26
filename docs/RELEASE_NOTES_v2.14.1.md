# PRISM-INSIGHT v2.14.1 — KIS Demo Verification + Two Defensive Fixes

> **Release Date**: 2026-05-26
> **PR**: #7 (`fix/kis-prefix-and-rate-limit` → `main`, `6fd2723`)

## 개요

v2.14.0 직후 사용자의 실제 paper-trading 자격증명으로 `trading/` 모듈을 종단간 검증하면서 발견한 false-positive 2건을 수정했습니다. 기본 동작은 보존하면서 운영자가 막히지 않게 우회 경로를 제공합니다.

**기능 추가 없음**. 둘 다 명확한 false-positive 수정 + 환경변수 opt-in. 회귀 위험 0.

## 주요 변경사항

### 1. `validate_credentials()` PSVT 접두사 휴리스틱 false positive 수정

**증상**: prism-insight는 paper key가 `PSVT*`로 시작해야 한다고 가정하지만, KIS는 paper key를 그 접두사 없이 발급하는 경우가 있습니다. 사용자의 paper key가 `PSnu`로 시작하지만 KIS demo 도메인(`openapivts.koreainvestment.com:29443`)에서 정상 작동하는 것을 직접 확인했습니다. 그럼에도 prism-insight가 사전 차단했습니다.

**원인** (`trading/kis_auth.py:842`):
```python
is_demo_key = app_key.startswith('PSVT')
if mode == 'vps' and not is_demo_key and app_key.startswith('PS'):
    return False, "CREDENTIAL MISMATCH! Using REAL app key (PS*) in DEMO mode..."
```

**수정** — `PRISM_KIS_BYPASS_PREFIX_CHECK` 환경변수 도입:

```bash
# 사용자의 paper key가 PSVT 접두사가 아닌 경우만 설정
PRISM_KIS_BYPASS_PREFIX_CHECK=true python stock_tracking_agent.py
```

- 환경변수 미설정 (디폴트): 기존 strict 검사 유지 — 회귀 위험 0
- 환경변수 truthy (`1`/`true`/`yes`/`on`): WARNING 로그 후 통과
- vps-mode 거부 메시지에 bypass 환경변수 안내 추가 (운영자 self-serve)

WARNING 로그 예시:
```
WARNING: PRISM_KIS_BYPASS_PREFIX_CHECK=true: allowing non-PSVT-prefixed key
in demo mode. Confirm the key was issued for paper trading.
```

silent allow 대신 명시적 경고를 남겨 misconfig가 숨지 않게 했습니다.

### 2. `get_current_price()` EGW00201 rate-limit 누락 fix

**증상**: KIS 시세 API는 초당 거래건수 cap이 있어 `EGW00201` ("초당 거래건수를 초과하였습니다")를 반환합니다. 5종목 연속 조회 시 1건이 실패하던 패턴을 직접 확인했습니다.

**원인** (`trading/domestic_stock_trading.py:226`):
```python
else:
    logger.error(f"Failed to get current price: {res.getErrorCode()} - ...")
    return None
```

다른 에러 코드와 동일하게 처리해 `None` 반환만 함 — caller(보통 multi-ticker 루프)는 복구할 방법이 없었습니다.

**수정** — `EGW00201` 전용 1회 자동 재시도:

```python
_RATE_LIMIT_CODE = "EGW00201"
max_attempts = 2
for attempt in range(1, max_attempts + 1):
    res = self._request(api_url, tr_id, params)
    if res.isOK():
        return result
    err_code = res.getErrorCode()
    if err_code == _RATE_LIMIT_CODE and attempt < max_attempts:
        backoff = 0.6 * attempt
        logger.warning(f"[{stock_code}] Rate-limited ({err_code}), retrying in {backoff:.1f}s")
        time.sleep(backoff)
        continue
    logger.error(f"Failed to get current price: {err_code} - ...")
    return None
```

- `EGW00201`만 retry — 다른 에러 코드 동작 변경 없음
- 0.6s × attempt 백오프 (현재 1회 retry, 향후 튜닝 여지)
- `max_attempts=2`로 hang 방지

## 변경된 파일

| 파일 | 변경 |
|------|------|
| `trading/kis_auth.py` | `validate_credentials()` — `PRISM_KIS_BYPASS_PREFIX_CHECK` 환경변수 처리 + WARNING 로깅 + 거부 메시지에 bypass 안내 추가 |
| `trading/domestic_stock_trading.py` | `get_current_price()` — `EGW00201` 자동 재시도 + 백오프 |
| `tests/test_kis_validation_and_rate_limit.py` | 7 신규 prompt-regression 테스트 |
| `docs/RUNBOOK_SCENARIO_5_DEMO_TRADING.md` | 트러블슈팅 표에 두 패턴 + EGW00133(1분당 1회 토큰 발급) 안내 추가 |

## 실증 검증 (2026-05-26)

사용자의 실제 KIS fake (paper) 자격증명으로 종단간 smoke test 수행. 자세한 결과:

### Test 결과

| 항목 | 결과 |
|------|------|
| `DomesticStockTrading` 초기화 + auth (bypass env 사용) | ✅ 토큰 발급 정상, WARNING 1회 로깅 |
| `get_current_price()` 5종목 연속 조회 (rapid-fire) | ✅ **5/5 성공** (fix 적용 전: 4/5) |
| `get_portfolio()` | ✅ 0 holdings (fake 계좌 정상) |
| `calculate_buy_quantity()` 다양한 금액 | ✅ 가격 30만원 > 매수액일 때 0 반환 |
| `AsyncTradingContext` async wrapper | ✅ 005935 삼성전자우 188,800원 |
| 예수금 직접 조회 | ✅ 10,000,000 KRW (모의 초기금) |

### 확인된 KIS demo 종목 시세 (2026-05-26 13:16 KST)

| 종목 | 가격 | 변동률 |
|------|------|--------|
| 005930 삼성전자 | 301,500 KRW | +3.08% |
| 000660 SK하이닉스 | 2,084,000 KRW | +7.37% |
| 005380 현대차 | 688,500 KRW | +5.11% |
| 035720 카카오 | 41,100 KRW | -1.79% |
| 035420 NAVER | 200,500 KRW | -1.23% |

### 자격증명 처리

검증 전 과정에서 자격증명을 외부 노출하지 않았습니다:
- `~/env.local.yml`에서 메모리로만 로드
- 출력 마스킹: `account=50****12`, `key=PSnu...auXc`
- 임시 `trading/config/kis_devlp.yaml` 생성 → `chmod 600` → `atexit` 핸들러로 즉시 삭제
- 토큰 캐시 + 암호화 키 파일도 종료 시 cleanup (매 실행 3개 secret-bearing 파일 정리)

## 테스트

`tests/test_kis_validation_and_rate_limit.py` 신규 — 7 cases:

| 테스트 | 검증 |
|--------|------|
| `test_validate_credentials_recognizes_bypass_env_var` | `PRISM_KIS_BYPASS_PREFIX_CHECK` 환경변수 + truthy set 파싱 |
| `test_validate_credentials_keeps_strict_default` | 환경변수 미설정 시 기존 strict 동작 유지 |
| `test_validate_credentials_bypass_logs_warning_not_silent_allow` | bypass 시 WARNING 로그 (silent allow 방지) |
| `test_bypass_hint_in_demo_mismatch_message` | vps 거부 메시지에 bypass 환경변수 안내 포함 |
| `test_get_current_price_retries_on_egw00201` | EGW00201 에서 retry 동작 |
| `test_retry_uses_increasing_backoff` | 백오프가 `attempt`에 따라 스케일 |
| `test_retry_caps_attempts_to_avoid_blocking` | `max_attempts`가 작은 정수 literal (hang 방지) |

전체 회귀: **81/81 pass** (v2.14.0의 74 + 신규 7).

## 마이그레이션

스키마 변경 없음, requirements 변경 없음. 재시작만 하면 됩니다.

기존 키가 `PSVT*` 접두사로 정상 작동 중이라면 추가 설정 불필요. paper key가 `PSVT` 접두사가 아닌데 demo 도메인에서 정상 동작하는 경우만 다음 설정:

```bash
# .env 또는 systemd unit 등
PRISM_KIS_BYPASS_PREFIX_CHECK=true
```

## 알려진 제한사항

1. **`EGW00133` (1분당 1회 토큰 발급 제한)는 별도 이슈**: 본 fix는 시세 API rate limit만 처리. 토큰은 정상 운영 시 자동 캐싱되므로 발생 빈도 낮음. 토큰 캐시 수동 삭제 후 재시도 시 발생 가능 — 1분 대기 필요.
2. **재시도 1회로 충분하지 않은 경우**: 매우 짧은 시간(<0.3s) 내 6+개 종목 조회 시 두 번째 시도도 실패할 수 있음. 현재 `max_attempts=2`로 fail-fast 우선. 향후 token bucket이나 explicit throttle 필요할 경우 별도 PR.
3. **`PRISM_KIS_BYPASS_PREFIX_CHECK`는 본인 책임**: KIS가 prefix 정책을 다시 강제할 경우 bypass가 켜져 있으면 잘못된 키로 인한 실패가 사후 발견됨. WARNING 로그를 모니터링해야 함.

## 텔레그램 공지

### 한국어

```
🔧 PRISM-INSIGHT v2.14.1 — KIS 검증 + false-positive fix 2건

v2.14.0 후 사용자의 실제 paper key로 trading/ 모듈을 검증하다
발견한 두 가지 false-positive를 수정했습니다.

🔑 변경 1 — PSVT 접두사 휴리스틱 완화
  paper key가 PSVT*로 시작하지 않아도 KIS demo는 정상 수락하는
  경우가 있습니다. PRISM_KIS_BYPASS_PREFIX_CHECK=true 환경변수로
  우회 가능. 기본은 strict 유지.

⏱️ 변경 2 — EGW00201 시세 rate-limit 자동 재시도
  5종목 연속 조회 시 1건이 실패하던 패턴 해결. get_current_price()
  가 EGW00201 받으면 0.6s 대기 후 1회 자동 재시도.

✅ 실증 검증: 5/5 시세 조회 + 예수금 조회 + async wrapper 모두 정상.

📂 docs/RELEASE_NOTES_v2.14.1.md 참고.
```

### English

```
🔧 PRISM-INSIGHT v2.14.1 — KIS Verification + Two False-Positive Fixes

Two patterns surfaced while smoke-testing the trading/ stack with a
real paper-trading credential after v2.14.0:

🔑 Fix 1 — relax the PSVT prefix heuristic
  KIS issues paper keys without the historical PSVT prefix.
  PRISM_KIS_BYPASS_PREFIX_CHECK=true env-var opts in. Default is
  unchanged (strict) — no regression for users whose keys still
  follow the convention.

⏱️ Fix 2 — auto-retry on EGW00201 in get_current_price()
  Per-second quote rate limit. Now retries once with 0.6s backoff.
  Bounded by max_attempts=2 so failures still surface promptly.

✅ End-to-end verification: 5/5 quotes + balance + async wrapper.
```

---

**Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>**
