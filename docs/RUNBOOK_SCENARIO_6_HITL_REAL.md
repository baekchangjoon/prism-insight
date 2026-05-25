# 런북 — 시나리오 6: 실거래 + Human-in-the-Loop 승인 (REAL + HitL)

> **대상**: 시나리오 5(DEMO)를 1-2주 이상 정상 운영했고 실제 돈으로 전환 준비된 사용자
> **위험도**: ⚠️ **최고 위험** — 실제 자금 손실 가능. 각 주문마다 사람 확정 필수.
> **전제**: HitL 승인 게이트(v2.13.0+)는 사용자가 매 주문 ✅/❌/📝 결정. 30분 무응답 시 자동 거절.
> **선행**: [RUNBOOK_SCENARIO_5_DEMO_TRADING.md](RUNBOOK_SCENARIO_5_DEMO_TRADING.md) 1-2주 정상 운영 검증 완료

---

## ⚠️ 시작 전 필수 체크리스트

다음을 **모두** 확인하지 않았다면 본 시나리오로 진입하지 마세요:

- [ ] 시나리오 5(DEMO)에서 **최소 10건 이상의 매수·매도 라운드 트립** 완료
- [ ] DEMO 운영 중 손실 사례를 검토하고 본인이 수용 가능한 수준이라 판단
- [ ] DEMO에서 `ENABLE_TRADE_APPROVAL=true`로도 **3-7일 검증** 완료 (DEMO 모드에서도 HitL 흐름 확인)
- [ ] Telegram **푸시 알림이 즉시 도달**하는 환경 (장중 30분 이내 응답 가능)
- [ ] **KIS 실전 API 키** 발급 + 보안 보관
- [ ] **본인이 잃어도 되는 금액**만 계좌에 입금 ("이 금액이 0원이 돼도 일상 영향 없음")
- [ ] 손절·익절 라인을 사전에 명확히 정의했고 본인이 동의

---

## 1. 사전 요구사항

### 시나리오 5에 추가
- **KIS 실전 계좌 + API 키**: 모의투자 키와 다름 (별도 발급)
  - 공식 가이드: https://apiportal.koreainvestment.com/apiservice-summary
- 시나리오 5의 매매 패턴이 본인 기대와 일치 확인됨
- v2.13.0 이상 (`approval/` 패키지 존재 확인: `ls approval/`)

```bash
# v2.13.0 이상인지 확인
grep "Version" CLAUDE.md | head -1
# → "Version**: 2.13.1" 이상이어야 함

ls approval/
# → handler.py, models.py, store.py, message.py 등 보여야 함
```

## 2. 보안 사전 점검

```bash
# 2.1 실전 키가 git에 절대 들어가지 않아야 함
git status --ignored | grep -E "kis_devlp|\.env"
# → kis_devlp.yaml, .env는 .gitignore에 있어야 함 (이미 등록되어 있음)

# 2.2 .env / kis_devlp.yaml 권한 강화
chmod 600 .env trading/config/kis_devlp.yaml

# 2.3 백업 (안전한 외부 위치)
# 자격증명만 분리해서 1password / 외부 암호화 vault에 보관
```

## 3. 단계적 전환 (필수 — 한 번에 모두 켜지 마세요)

### Step A: DEMO + HitL 게이트만 (3일)

먼저 **demo 모드 그대로** + HitL 게이트만 켜서 흐름을 익힙니다.

```bash
# .env
ENABLE_TRADE_APPROVAL=true
APPROVAL_DB_PATH=trade_approvals.db
APPROVAL_TIMEOUT_SECONDS=1800   # 30분
AUTO_STOP_LOSS_BYPASS=false      # ⭐ stop-loss도 사람 확정 받기
```

```yaml
# trading/config/kis_devlp.yaml — 그대로 demo 유지
default_mode: demo
accounts:
  - id: primary
    mode: demo                # ⭐ 아직 demo
    app_key: "..."            # 모의투자 키
    ...
```

**기대 흐름**:
1. 매수 신호 발생 → Telegram에 승인 카드 도착:
   ```
   🟡 매수 승인 요청
   삼성전자 (005930)
   진입가: 70,000 원
   손절가: 66,500 원 / 목표가: 78,000 원
   투자금액: 500,000 원
   신뢰도: 7점
   
   AI 근거:
   • 거래량 폭증 + 외국인 매수세
   • 컨센서스 목표가 11% 상회 여력
   
   만료: 14:32:15 (30분 후 자동 거절)
   [✅ 매수 승인] [❌ 거절] [📝 금액 수정]
   ```
2. ✅/❌/📝 중 선택, 또는 30분 무응답 → 자동 거절
3. ✅ 시 → 모의 KIS 주문 발송 + `trade_approvals` SQLite에 기록
4. 📝 시 → `/retry_<승인ID> <새금액>` 입력으로 새 카드 발급 (v2.13.1)

**Step A 검증** (3일 후):
```bash
sqlite3 trade_approvals.db "
SELECT decision, COUNT(*) FROM trade_approvals
GROUP BY decision ORDER BY 2 DESC;
"
# → APPROVED / REJECTED / EXPIRED / MODIFY_REQUESTED 분포 확인

# 만료(EXPIRED) 비율이 50% 넘으면 → 응답 가능한 시간대에만 운영 권장
# 또는 APPROVAL_TIMEOUT_SECONDS 늘림 (예: 3600 = 1시간)
```

### Step B: 소액 실전 + 강한 보수화 (1주)

DEMO HitL 흐름이 익숙해지면 **소액**으로 실전 전환.

```bash
# 시나리오 5의 stock_tracking_agent cron을 일시 정지
crontab -e   # 매매 관련 라인 주석 처리
```

```yaml
# trading/config/kis_devlp.yaml
default_unit_amount: 100000      # ⭐ 10만원으로 축소 (시나리오 5의 1/5)
default_mode: real               # ⭐ demo → real
accounts:
  - id: primary
    mode: real                   # ⭐ demo → real
    app_key: "PSxxx..."          # ⭐ 실전 KIS app_key (모의와 다름!)
    app_secret: "..."            # ⭐ 실전 app_secret
    account: "12345678"          # ⭐ 실전 계좌번호 (앞 8자리)
    product: "01"
    market: "kr"
    primary: true
    buy_amount_krw: 100000       # 이 계좌 매수액 명시
```

```bash
# .env (그대로 유지)
ENABLE_TRADE_APPROVAL=true
AUTO_STOP_LOSS_BYPASS=false
```

```bash
# 토큰 강제 재발급 (캐시 무효화)
rm -f trading/config/KIS$(date +%Y%m%d)*

# 실전 잔고 조회로 연결 검증
python -c "
from trading import kis_auth as ka
ka.set_active_account(ka.getEnv()['accounts'][0])
b = ka.get_account_balance()
print('Account:', b.get('dnca_tot_amt', 'NO DATA'))
"
# → 입금한 금액 (또는 0원)이 표시되어야 함
```

**Cron 재개 (보수화 적용)**:
```cron
# 매수는 장 시작 09:00에 1회만
0 9 * * 1-5  cd /path/to/prism-insight && python3 stock_tracking_agent.py >> logs/tracking.log 2>&1

# 매도 판단은 10분 주기 (HitL이 매번 사람 확정 필요)
*/10 9-15 * * 1-5  cd /path/to/prism-insight && python3 stock_tracking_agent.py >> logs/tracking.log 2>&1
```

> 시나리오 5처럼 5분 주기로 돌리지 마세요 — HitL 카드가 5분마다 폭주합니다.

### Step C: 점진 확대

Step B를 1주 정상 운영 후, 본인 판단에 따라:
- `default_unit_amount`을 단계적으로 증액 (10만 → 30만 → 50만 → ...)
- `MAX_SLOTS` 조정 (코드 수정 필요, `stock_tracking_agent.py`)
- 손실 누적 시 즉시 Step A로 복귀 (`default_mode: demo`)

## 4. HitL 운영 패턴

### 4.1 📝 금액 수정 흐름 (v2.13.1)

매수 카드에서 📝 → Telegram에 안내:
```
📝 금액 수정 요청을 받았습니다.
새 금액(원)으로 다음 명령을 입력해주세요:
/retry_abc123def456 <새 금액>
예: /retry_abc123def456 300000
```

사용자가 `/retry_abc123def456 300000` 또는 `/retry_abc123def456 300,000` (쉼표 허용) 입력:
1. 동일 종목·동일 조건이지만 **새 `approval_id`** 로 fresh 카드 발급
2. 원본 record는 `MODIFY_REQUESTED` 상태로 감사 보존
3. 새 카드도 30분 timeout

### 4.2 자동 손절 예외 (선택)

```bash
# .env
AUTO_STOP_LOSS_BYPASS=true     # ⭐ 위험: stop-loss 매도는 즉시 실행
```

활성화 시:
- 매수는 매번 사람 확정 필요
- 손절선 도달 매도는 **사람 확정 없이 즉시 KIS 발송** → 큰 손실 방지
- `trade_approvals`에 `AUTO_EXECUTED` 상태로 기록

> Step B 1주 검증 후에만 켜는 것을 권장합니다. 처음에는 손절도 사람 확정으로.

### 4.3 일시 중단

```bash
# .env에서 한 줄만 토글
sed -i 's/ENABLE_TRADE_APPROVAL=true/ENABLE_TRADE_APPROVAL=false/' .env
```

→ ENABLE_TRADE_APPROVAL=false면 자동 매매 시도 자체가 차단됩니다 (HitL 게이트가 없으면 매매 안 함).

> 단, 시나리오 5처럼 HitL 없는 직접 KIS 호출 코드 경로는 ENABLE_TRADE_APPROVAL과 무관하게 작동합니다. 완전 중단 원하면 cron도 함께 멈추세요.

## 5. 감사 추적 (Audit Trail)

모든 매매 승인 결정은 `trade_approvals` SQLite에 영구 보존:

```sql
-- 최근 결정 20건
SELECT proposed_at, ticker, stock_name, side,
       proposed_amount_krw, final_amount_krw,
       decision, decided_by, decided_at,
       order_no, pnl_amount, pnl_rate
FROM trade_approvals
ORDER BY proposed_at DESC
LIMIT 20;

-- 결정별 분포 (1주)
SELECT decision, COUNT(*), SUM(final_amount_krw)
FROM trade_approvals
WHERE proposed_at >= date('now', '-7 days')
GROUP BY decision;

-- MODIFY_REQUESTED → /retry 전환율
SELECT
  SUM(CASE WHEN decision='MODIFY_REQUESTED' THEN 1 ELSE 0 END) AS modify_count,
  (SELECT COUNT(*) FROM trade_approvals
   WHERE proposed_at >= (SELECT MIN(proposed_at) FROM trade_approvals
                         WHERE decision='MODIFY_REQUESTED')
   AND decision='APPROVED'
   AND ticker IN (SELECT ticker FROM trade_approvals WHERE decision='MODIFY_REQUESTED')
  ) AS retry_approved
FROM trade_approvals
WHERE proposed_at >= date('now', '-7 days');
```

## 6. 일일 운영 체크리스트

장 마감 후 (16:00 이후) 5분이면 충분:

- [ ] Telegram 매매 알림 누락 없는지 (앱 알림 OFF 안 됨)
- [ ] `trade_approvals.db`에서 EXPIRED 비율 점검 (≤ 20% 권장)
- [ ] `trading_history`에서 오늘 수익률 합계
- [ ] `firecrawl_status` 마커 확인 (#286 모니터링)
- [ ] KIS API 호출 실패 로그 확인 (`logs/tracking.log` `ERROR` grep)

```bash
# 일일 요약 스크립트 예시
bash -c '
echo "=== $(date +%Y-%m-%d) 일일 요약 ==="
sqlite3 trade_approvals.db "SELECT decision, COUNT(*), SUM(final_amount_krw) FROM trade_approvals WHERE date(proposed_at)=date(\"now\") GROUP BY decision;"
echo
sqlite3 stock_tracking_db "SELECT ticker, profit_rate, sell_reason FROM trading_history WHERE date(sell_date)=date(\"now\");"
echo
grep -c "ERROR\|FAIL" logs/tracking.log
'
```

## 7. 트러블슈팅

| 증상 | 원인 / 해결 |
|------|-----------|
| Telegram 승인 카드 안 옴 | `ENABLE_TRADE_APPROVAL=true` 확인 + Bot이 채널 admin인지 확인 |
| ✅ 눌렀는데 주문 안 됨 | `trade_approvals.execution_result_json` 확인 — KIS 응답 에러 코드 grep |
| EXPIRED 비율 50% 이상 | 사용자가 응답 못하는 시간대에 신호 발생 — `APPROVAL_TIMEOUT_SECONDS` 늘리거나 매매 시간대 좁히기 |
| 📝 후 `/retry_...` 입력해도 무반응 | v2.13.0에서는 핸들러 없었음. v2.13.1+ 필수 — `git pull` |
| `/retry_<id> 100000` 후 "이미 처리됨" | stash 이미 소진(다른 사람이 처리) 또는 timeout. 새 매수 카드를 기다리세요 |
| 손절선 돌파했는데 매도 안 됨 | `AUTO_STOP_LOSS_BYPASS=false`일 때는 사람 확정 필요. 매도 카드 ✅ 누르세요 |
| `MODIFY_REQUESTED → REJECTED` 패턴 반복 | stash 시간 만료. `/retry_` 빨리 입력하거나 timeout 늘리기 |
| 실전 키인데 401 unauthorized | `vps` 도메인(`openapivts...`)에 실전 키 보낸 것. `default_mode: real`일 때 `prod` 도메인 사용 확인 |

## 8. 비활성화 / 롤백

### 즉시 일시중단 (HitL만 비활성화, 코드 변경 없음)
```bash
sed -i 's/ENABLE_TRADE_APPROVAL=true/ENABLE_TRADE_APPROVAL=false/' .env
# → ENABLE_TRADE_APPROVAL=false면 모든 매매 시도 차단 (시나리오 5 패턴도 별도로 멈춰야 함)
```

### 시나리오 5로 복귀 (DEMO + HitL OFF)
```yaml
# kis_devlp.yaml
default_mode: demo
accounts:
  - mode: demo                # 모든 계좌 demo로
    app_key: "..."            # 모의투자 키로 교체
    ...
```
```bash
# .env
ENABLE_TRADE_APPROVAL=false
```
```bash
# 실전 토큰 캐시 폐기
rm -f trading/config/KIS*
```

### 완전 중단
```bash
crontab -e   # 모든 PRISM 라인 주석 처리
# bot daemon도 중지
```

## 9. 한도 / 제약 (시나리오 6 모드에서)

| 항목 | 권장 초기값 | 비고 |
|------|------------|------|
| `default_unit_amount` | 100,000원 | Step B 시작 |
| `MAX_SLOTS` | 5 (시나리오 5의 절반) | `stock_tracking_agent.py:MAX_SLOTS` |
| `APPROVAL_TIMEOUT_SECONDS` | 1800 (30분) | 응답 시간대 보장 가능할 때 |
| `AUTO_STOP_LOSS_BYPASS` | false → 1주 후 true | Step B 단계 |
| 일일 손실 한도 | 매수액 × 손절률 × MAX_SLOTS | 본인 정신 건강에 맞춰 |

## 10. 알려진 제한사항 (v2.13.1 기준)

1. **수정 stash 인메모리**: 봇 재시작 시 진행 중인 MODIFY 흐름 유실. 사용자는 새 AI 신호 대기 필요.
2. **HitL 없는 직접 매매 경로 잔존**: `stock_tracking_agent.py`의 일부 코드 경로는 ENABLE_TRADE_APPROVAL과 무관 — 완전 멈춤 원하면 cron 정지.
3. **다계좌 fan-out**: HitL 게이트는 단일 승인 카드로 모든 계좌에 fan-out. 계좌별 개별 승인은 미지원 (v2.14+ 후보).
4. **모의/실전 KIS 응답 차이**: `hts_kor_isnm` 등 일부 필드는 모의에서 빈값 반환. 운영 코드의 분기 로직 점검.

## 11. 사용자 마음가짐 가이드

- **모든 매매는 본인 책임**. AI는 보조 도구이고, 최종 결정은 ✅ 누른 사용자.
- **확신 없는 카드는 ❌**. AI Score 7/10이라도 본인이 사정 모르는 시점이면 거절.
- **MODIFY로 작게 시작**. 제안 금액의 50%로 retry하는 습관.
- **연속 손실 5건이면 1주 휴식**. DEMO로 복귀 후 패턴 점검.
- **결과를 기록**. `/journal` 명령으로 매도 후 본인 회고도 추가.

---

**관련**: [RUNBOOK_SCENARIO_5_DEMO_TRADING.md](RUNBOOK_SCENARIO_5_DEMO_TRADING.md) | [RELEASE_NOTES_v2.13.0.md](RELEASE_NOTES_v2.13.0.md) (HitL 설계) | [RELEASE_NOTES_v2.13.1.md](RELEASE_NOTES_v2.13.1.md) (`/retry_` 핸들러) | [CLAUDE.md](../CLAUDE.md)
