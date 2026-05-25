# 런북 — 시나리오 4: 셀프 호스트 분석 (매매 없음)

> **대상**: PRISM-INSIGHT를 직접 설치해서 KR/US 주식 분석 보고서만 생성하고 싶은 사용자
> **위험도**: 낮음 (실거래 없음, 텔레그램 전송 선택)
> **소요 시간**: 첫 설치 30-60분, 이후 단일 보고서 생성 5-10분
> **선행 문서**: [docs/SETUP.md](SETUP.md) (전체 설치 가이드)

## 1. 사전 요구사항

### 필수
- Python 3.10+ (3.11 권장)
- Git
- ~5GB 디스크 (chromium + reports + DB)
- macOS / Linux / WSL2 (Windows native 미검증)

### 필수 API 키 (조합 가능 — v2.14.0+)

분석 에이전트별로 provider를 선택할 수 있습니다 ([v2.14.0 릴리스 노트](RELEASE_NOTES_v2.14.0.md) 참조). 가장 흔한 3가지 조합:

| 조합 | 필요한 키 | 비고 |
|------|----------|------|
| **올-OpenAI (기본)** | OpenAI + Firecrawl + (선택) Anthropic | 디폴트. Anthropic 없으면 Investment Strategist도 OpenAI fallback |
| **올-OpenAI w/ ChatGPT OAuth** (v2.7.0+) | ChatGPT Plus/Pro 구독 + Firecrawl + Anthropic | OpenAI API 비용 0원. `PRISM_OPENAI_AUTH_MODE=chatgpt_oauth` |
| **하이브리드 (Gemini + Anthropic)** | Google AI Studio + Anthropic + Firecrawl | 분석 6종을 Gemini로, Investment Strategist만 Claude. 비용 절감 |
| **올-Gemini** | Google AI Studio + Firecrawl | Anthropic 없음. Investment Strategist 품질 검증 필요 |

- **Google AI Studio 키** (https://aistudio.google.com/apikey) — Gemini 3.5 Flash GA(권장) 또는 Pro. Free tier 있음. v2.14.0 LLMProvider 경로로 Level 1-4 검증 완료 ([HANDOFF_GEMINI_VERIFICATION.md](HANDOFF_GEMINI_VERIFICATION.md))
- **Firecrawl API 키** (https://firecrawl.dev) — 기업현황/뉴스 스크랩. Free tier 500 credits/월. LLM 아니므로 대체 불가.
- **xAI Grok 키** (https://x.ai/api) — 선택. OpenAI 호환 API로 라우팅

### 선택
- **KRX 직접 로그인** ID/PW 또는 카카오 SNS 로그인 — `kospi_kosdaq` MCP 서버 인증용 (없으면 일부 시세 데이터 누락)
- **Telegram Bot Token** + Channel ID — 결과 전송 (없으면 `--no-telegram`)
- **Adanos API 키** — US 소셜 센티먼트 (US 분석 시)

## 2. 사전 점검

```bash
python3 --version    # 3.10 이상이어야 함
git --version
node --version       # Firecrawl/Perplexity MCP 서버용 (npx)
which npx
df -h .              # 최소 5GB 여유
```

## 3. 설치

```bash
# 3.1 클론
git clone https://github.com/dragon1086/prism-insight
cd prism-insight

# 3.2 의존성
pip install -r requirements.txt

# 3.3 Chromium (PDF 생성)
python3 -m playwright install chromium

# 3.4 한글 폰트 (Linux만)
# Ubuntu/Debian: sudo apt install fonts-nanum && fc-cache -fv
# RHEL/Rocky/CentOS: sudo dnf install google-nanum-fonts && fc-cache -fv
# macOS: 시스템 한글 폰트 사용
```

## 4. 설정 파일

### 4.1 핵심 설정 (필수)

```bash
cp mcp_agent.config.yaml.example mcp_agent.config.yaml
cp mcp_agent.secrets.yaml.example mcp_agent.secrets.yaml
```

**`mcp_agent.secrets.yaml`** 편집 — API 키 채우기 (사용할 provider만):
```yaml
openai:
  api_key: "sk-..."          # OpenAI API
anthropic:
  api_key: "sk-ant-..."      # Anthropic
google:
  api_key: "AIza..."         # (v2.14.0+) Gemini 사용 시
# grok:
#   api_key: "xai-..."       # (v2.14.0+) Grok 사용 시
#   base_url: "https://api.x.ai/v1"
# firecrawl key는 mcp_agent.config.yaml의 servers.firecrawl.env에 입력
```

**`mcp_agent.config.yaml`** 편집:
```yaml
mcp:
  servers:
    firecrawl:
      env:
        FIRECRAWL_API_KEY: "fc-..."
    kospi_kosdaq:
      env:
        KRX_ID: "your_krx_id"
        KRX_PW: "your_krx_password"
        KRX_LOGIN_METHOD: "krx"     # 또는 "kakao"
```

> `read_timeout_seconds: 120`이 firecrawl·webresearch에 설정되어 있는지 확인 (v2.13.1 권장값).

### 4.2 LLM provider 선택 (v2.14.0+, 선택)

기본은 pre-v2.14 하드코딩 (OpenAI 분석 + Anthropic 전략). 다른 provider로 바꾸려면 `mcp_agent.config.yaml` 끝에 `llm:` 섹션 추가:

```yaml
# 예시 A — 하이브리드 (분석은 Gemini, Investment Strategist는 Claude)
# v2.14.0 검증 완료 모델: gemini-3.5-flash (GA)
llm:
  default:
    provider: google
    model: gemini-3.5-flash
  roles:
    strategist:
      provider: anthropic
      model: claude-sonnet-4-6
    insight:
      provider: anthropic
      model: claude-sonnet-4-6
    trading:
      provider: openai       # OpenAI Responses API 유지
      model: gpt-5.5

# 예시 B — 올-Gemini (Anthropic 키 없을 때)
llm:
  default:
    provider: google
    model: gemini-3.5-flash
  roles:
    strategist:
      model: gemini-pro-latest  # 통합 에이전트는 더 큰 모델
```

환경변수로 빠르게 override도 가능:
```bash
# 모든 role을 Gemini로 강제
PRISM_LLM_PROVIDER=google python demo.py 005930

# strategist만 특정 모델
PRISM_LLM_PROVIDER_STRATEGIST=anthropic PRISM_LLM_MODEL_STRATEGIST=claude-opus-4-7 python demo.py 005930
```

지원 provider: `openai` · `anthropic` · `google` · `grok`. 각 role의 built-in default와 설정 우선순위는 [v2.14.0 릴리스 노트](RELEASE_NOTES_v2.14.0.md) 참조.

### 4.2 환경변수 (선택)

```bash
cp .env.example .env 2>/dev/null || touch .env
```

**`.env`** 핵심 항목:
```bash
# Telegram (있으면 자동 전송, 없으면 --no-telegram 필수)
TELEGRAM_BOT_TOKEN="123456:ABC..."
TELEGRAM_CHANNEL_ID="-1001234567890"

# OpenAI 인증 모드
PRISM_OPENAI_AUTH_MODE=api_key       # 또는 chatgpt_oauth

# 분석 모드 (선택)
ENABLE_TRADING_JOURNAL=false         # 시나리오 4에서는 false 유지

# 디버그
PYTHONUNBUFFERED=1
```

### 4.3 ChatGPT OAuth (API 키 대신 구독 사용 시)

```bash
# 브라우저 OAuth 1회 (이후 자동 갱신)
python -m cores.chatgpt_proxy.oauth_login

# 재인증 (계정 전환 / 토큰 만료)
python -m cores.chatgpt_proxy.oauth_login --force

# .env에 추가
echo "PRISM_OPENAI_AUTH_MODE=chatgpt_oauth" >> .env
```

## 5. 첫 실행 — 단일 종목 분석

```bash
# KR 단일 종목 (텔레그램 전송 없음, 가장 안전)
python demo.py 005930

# US 단일 종목
python demo.py AAPL --market us
```

**기대 동작**:
1. 6개 분석 에이전트가 sequential 실행 (Technical → Trading Flow → Financial → Industry → News → Market)
2. Investment Strategist가 통합 보고서 작성
3. `reports/` 디렉토리에 markdown + PDF 생성
4. (Telegram 설정 시) 채널에 PDF 전송

**시간**: 정상 환경에서 5-10분. 처음 실행은 npx로 MCP 서버 다운로드 때문에 1-2분 추가.

## 6. 검증 체크리스트

```bash
ls -la reports/                         # PDF + .md 파일 확인
ls -la logs/                            # 에이전트별 로그 확인

# 보고서 핵심 섹션 확인
grep "기업 현황 분석\|Technical Analysis\|Investment Strategy" reports/*.md | head

# v2.13.1 firecrawl status 마커 (기업현황 섹션이 잘 수집됐는지)
grep "firecrawl_status" reports/*.md
# → "ok" 정상, "partial"/"failed"이면 firecrawl 키/네트워크 점검
```

## 7. 일상 운영

### 7.1 매일 자동 분석 (배치 모드)

```bash
# 장 시작 전 급등 종목 자동 선정 + 보고서 일괄 생성
python stock_analysis_orchestrator.py --mode morning --no-telegram

# Telegram 전송 포함
python stock_analysis_orchestrator.py --mode morning

# 다국어 broadcast
python stock_analysis_orchestrator.py --mode morning --broadcast-languages en,ja,zh
```

**Cron 예시** (오전 8시 KR 분석):
```cron
0 8 * * 1-5  cd /path/to/prism-insight && /usr/bin/python3 stock_analysis_orchestrator.py --mode morning >> logs/morning.log 2>&1
```

자세한 crontab 설정은 [utils/CRONTAB_SETUP_ko.md](../utils/CRONTAB_SETUP_ko.md).

### 7.2 급등 후보만 추출 (보고서 생성 X)

```bash
python trigger_batch.py morning INFO
# → trigger_results_morning_YYYYMMDD.json
```

### 7.3 US 모듈

```bash
python prism-us/us_stock_analysis_orchestrator.py --mode morning
python prism-us/us_trigger_batch.py morning INFO
```

### 7.4 주간 인사이트 리포트

```bash
python weekly_insight_report.py --dry-run                 # 콘솔만
python weekly_insight_report.py --broadcast-languages en,ja
```

## 8. 모니터링

| 위치 | 내용 |
|------|------|
| `logs/morning.log` | 배치 실행 로그 |
| `logs/subprocess/report_<ticker>_<timestamp>.log` | 종목별 분석 subprocess 상세 로그 |
| `reports/*.md`, `reports/*.pdf` | 생성된 보고서 |
| `analysis_performance_tracker` 테이블 | 분석 종목의 7/14/30일 사후 성과 (SQLite) |

```bash
# 최근 보고서 firecrawl 성공률
grep -h "firecrawl_status:" reports/*_$(date +%Y%m%d)*.md | sort | uniq -c
```

## 9. 트러블슈팅

| 증상 | 원인 / 해결 |
|------|-----------|
| `could not convert string to float: ''` | v2.2에서 fix됨 — `git pull` |
| Playwright PDF 실패 | `python3 -m playwright install chromium` 재실행 |
| 한글 깨짐 (Linux) | `sudo dnf install google-nanum-fonts && fc-cache -fv` |
| `2.1 기업현황` 섹션이 짧고 데이터 없음 | v2.13.1: 보고서 끝의 `firecrawl_status` 확인. `failed`이면 FIRECRAWL_API_KEY/credits 점검 |
| `/ask` 결과가 1년 전 데이터 | v2.13.1에서 fix — `git pull` |
| ChatGPT OAuth 404 | `python -m cores.chatgpt_proxy.oauth_login`로 재인증 |
| 종목명 한글 없음 (모의투자) | KIS 모의투자 환경 한계 — 실전 도메인은 정상 |

전체 트러블슈팅: [docs/CLAUDE_TROUBLESHOOTING.md](CLAUDE_TROUBLESHOOTING.md).

## 10. 비활성화 / 정리

```bash
# 모든 분석 중단 (cron 해제만 하면 됨)
crontab -e   # PRISM 라인 주석 처리

# 데이터/캐시 정리 (선택)
rm -rf reports/ logs/
rm -f stock_tracking_db archive.db trade_approvals.db
```

> **분석만 한 상태에서는 영구 상태가 거의 없습니다**. `stock_holdings`/`trading_history`도 비어 있을 것입니다 (시나리오 5/6에서만 생성).

## 11. 다음 단계

- 매매까지 자동화하고 싶다면 → [RUNBOOK_SCENARIO_5_DEMO_TRADING.md](RUNBOOK_SCENARIO_5_DEMO_TRADING.md)
- 보고서 형식·에이전트 커스터마이즈 → [CLAUDE_TASKS.md](CLAUDE_TASKS.md)
- 매매 일지 활용 (`/journal`) → [TRADING_JOURNAL.md](TRADING_JOURNAL.md)

---

**관련 문서**: [SETUP.md](SETUP.md) | [CLAUDE.md](../CLAUDE.md) | [CLAUDE_TROUBLESHOOTING.md](CLAUDE_TROUBLESHOOTING.md)
