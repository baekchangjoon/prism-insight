# 핸드오프 — Gemini Provider 검증 진행 상황

> **마지막 업데이트**: 2026-05-25
> **상태**: Level 1-4 통과 (v2.14.0 플러그 검증 완료). Level 5 (실제 `demo.py 005930`)는 추가 자격증명 필요로 보류.
> **관련 PR**: #5 (`feat/multi-llm-provider` → `main`)
> **관련 릴리스 노트**: [RELEASE_NOTES_v2.14.0.md](RELEASE_NOTES_v2.14.0.md)

본 문서는 다음 세션(또는 다른 작업자)이 Gemini provider로 prism-insight를 실제 종단간 실행하려 할 때 무엇이 검증되어 있고 무엇이 더 필요한지를 빠르게 파악하기 위한 핸드오프 가이드입니다.

## 1. 지금까지 검증된 것

### 사용자 환경
- `~/env.local.yml`에 `gemini_api_key` 항목 존재 — Google AI Studio 발급 키
- 키는 메모리에만 보관하고 어떤 파일에도 저장되지 않음 (마스킹: `AIzaSy...Jt1o (39 chars)`)

### 가용 Gemini 모델 (이 키로 확인된 것)
사용자가 추측한 `gemini-3.5-flash`가 실제 GA 사용 가능. 다른 candidates:

| 모델 | 입력 limit | 비고 |
|------|----------|------|
| `gemini-3.5-flash` ⭐ | 1,048,576 | 권장 default. 비용·속도 최적 |
| `gemini-flash-latest` | 1,048,576 | 최신 alias |
| `gemini-pro-latest` | 1,048,576 | strategist 후보 |
| `gemini-3-pro-preview` | 1,048,576 | preview |
| `gemini-3-flash-preview` | 1,048,576 | preview |

### 통과한 검증 레벨

| Level | 항목 | 검증 방법 | 결과 |
|-------|------|----------|------|
| 1 | `gemini-3.5-flash` REST API 호출 | `generativelanguage.googleapis.com/v1beta/models/.../generateContent` 직접 POST | ✅ 정상 응답, usage 정상 |
| 2 | `mcp_agent.workflows.llm.augmented_llm_google.GoogleAugmentedLLM` import | `pip install` 후 import | ✅ — `google-genai` SDK 별도 설치 필요했음 |
| 3 | `LLMProvider` 라우팅 | `PRISM_LLM_PROVIDER_*` env 우선순위, `clean()` strip, trading `prefer_responses_api=True` 시 google fallback | ✅ 모든 동작 의도대로 |
| 4 | prism-insight pattern: MCPApp + Agent + `LLMProvider.get_llm_class("analysis")` → `generate_str` | stdin 임시 Python으로 종단간 호출 (MCP tool 없이) | ✅ Korean prompt에 Korean 응답 1.4초 |

### Level 4 검증 명령 (재현용)

```bash
GOOGLE_API_KEY="<from env.local.yml>" \
PRISM_LLM_PROVIDER=google \
PRISM_LLM_MODEL=gemini-3.5-flash \
python3.11 -c "
import asyncio
from mcp_agent.app import MCPApp
from mcp_agent.agents.agent import Agent
from mcp_agent.workflows.llm.augmented_llm import RequestParams
from cores.llm.provider import LLMProvider, clean

async def main():
    app = MCPApp(name='gemini_smoke')
    async with app.run():
        agent = Agent(name='smoke', instruction='Answer in Korean.', server_names=[])
        async with agent:
            llm = await agent.attach_llm(LLMProvider.get_llm_class('analysis'))
            print(await llm.generate_str(
                message='연결 테스트',
                request_params=clean(RequestParams(
                    model=LLMProvider.get_model('analysis'),
                    reasoning_effort='none', maxTokens=200,
                ), role='analysis'),
            ))
asyncio.run(main())
"
```

## 2. Level 5 (실제 `demo.py 005930`)로 가려면

### 추가로 필요한 자격증명 (`~/env.local.yml`에 현재 없음)

| 키 | 어디서 발급 | 비용 | 대체 가능성 |
|---|------------|------|------------|
| `firecrawl_api_key` | https://firecrawl.dev | Free tier 500 credits/월 | ❌ LLM 아님, MCP 서버용 — 대체 불가 |
| `krx_id` + `krx_pw` (또는 카카오 SNS) | KRX 거래소 회원가입 | 무료 | KRX 데이터 일부 fallback 가능하지만 시세 누락 |
| `anthropic_api_key` (선택) | https://console.anthropic.com | 사용량 기반 | v2.14.0 config로 strategist도 gemini-3.5-pro로 대체 가능 |

### 의존성 설치 (필요한 것만)

Level 5 실행에 필요한 최소 패키지 (사용자 머신에 mcp-agent + google-genai는 이미 설치됨):

```bash
cd /Users/changjoonbaek/github_prism-insight/prism-insight
python3.11 -m pip install -r requirements.txt
python3.11 -m playwright install chromium
```

> requirements.txt는 50+ 패키지 포함 (pandas, pykrx, playwright, telegram-bot 등). 시간 5-10분, 디스크 ~3GB.

### 환경 변수 / config 설정

**옵션 A — 올-Gemini (Anthropic 키 없음)**

`mcp_agent.config.yaml` 끝에 추가:
```yaml
llm:
  default:
    provider: google
    model: gemini-3.5-flash
  roles:
    strategist:
      model: gemini-pro-latest    # 통합 에이전트는 더 큰 모델
    trading:
      provider: google             # Responses API fallback
      model: gemini-3.5-flash
```

`mcp_agent.secrets.yaml`:
```yaml
google:
  api_key: "<gemini_api_key from env.local.yml>"
```

**옵션 B — 하이브리드 (Anthropic 키도 있을 때, 품질 안정성 우선)**

```yaml
llm:
  default:
    provider: google
    model: gemini-3.5-flash
  roles:
    strategist:
      provider: anthropic           # Claude 유지
      model: claude-sonnet-4-6
    insight:
      provider: anthropic
      model: claude-sonnet-4-6
    trading:
      provider: openai              # OpenAI Responses 유지 (별도 키 필요)
      model: gpt-5.5
```

### 실행 명령

```bash
# 단일 종목 (텔레그램 전송 없음, 가장 안전)
python3.11 demo.py 005930

# 또는 환경변수로 일회성 override (config 안 만들고)
GOOGLE_API_KEY="<from env.local.yml>" \
PRISM_LLM_PROVIDER=google \
PRISM_LLM_MODEL=gemini-3.5-flash \
python3.11 demo.py 005930
```

### 예상 동작 (Level 5)

1. **시작 (~10초)**: MCPApp 초기화, firecrawl·kospi_kosdaq MCP 서버 npx로 spawn
2. **6개 분석 에이전트 sequential 실행 (각 30초~2분)**:
   - Technical Analyst (kospi_kosdaq MCP)
   - Trading Flow Analyst (kospi_kosdaq MCP)
   - Financial Analyst (firecrawl_scrape WiseReport)
   - Industry Analyst (firecrawl_scrape WiseReport)
   - News Analyst (firecrawl_search + firecrawl_scrape)
   - Market Analyst (cached if same session)
3. **Investment Strategist 통합 (~2분)**: 옵션 A면 gemini-pro-latest, 옵션 B면 claude-sonnet-4-6
4. **Executive Summary (~30초)**
5. **PDF 생성 (~30초)**: playwright + chromium
6. **출력**: `reports/005930_*_*_morning_*.md` + `.pdf`

총 5-10분.

### 검증 체크리스트

```bash
# 보고서 생성 확인
ls -la reports/005930*

# v2.13.1 firecrawl status (Gemini가 function calling 제대로 했는지 간접 확인)
grep "firecrawl_status" reports/005930*.md
# → "ok" 정상. "partial"/"failed"면 Gemini의 firecrawl_scrape 호출이 OpenAI와 다른 포맷이라
#   재시도가 실패한 케이스 — 다음 세션에서 mcp-agent의 google 어댑터 동작 살펴봐야 함

# 각 섹션 모델 사용 확인 (로그)
grep "model=" logs/subprocess/report_005930_*.log | head -10
```

## 3. 알려진 / 예상 가능한 이슈

### 검증되지 않은 영역

1. **Function calling 호환성**: prism-insight 6개 분석 에이전트는 모두 MCP tool calling (firecrawl_scrape, get_historical_stock_prices 등)을 사용합니다. Gemini의 function calling 포맷은 OpenAI/Anthropic과 다릅니다. mcp-agent의 `GoogleAugmentedLLM`이 이 변환을 처리한다고 가정하지만 **실제 종단간 검증은 안 됨**.
2. **Responses API fallback의 JSON 응답 안정성**: Buy/Sell Specialist는 OpenAI Responses API로 structured JSON을 받습니다. google 사용 시 standard chat completion으로 fallback되므로 JSON 파싱 실패율이 올라갈 수 있음. `cores/utils.py`의 `parse_llm_json`이 fallback 처리하지만 신뢰도 검증 필요.
3. **`gemini-pro-latest`의 strategist 품질**: Investment Strategist는 페르소나 검토(O'Neil/Minervini/Druckenmiller/Buffett/Quant) 기반 정교한 reasoning이 필요. Claude Sonnet 4.6 디폴트와의 품질 비교는 demo 모드 1-2주 운영 후에야 가능.

### 빠른 트러블슈팅

| 증상 | 원인 / 해결 |
|------|-----------|
| `ModuleNotFoundError: google.genai` | `python3.11 -m pip install google-genai` |
| `ModuleNotFoundError: mcp_agent` | `pip install -r requirements.txt` (또는 직접 git+ URL 설치) |
| `Gemini API: Quota exceeded` | Free tier 한도 초과 — Google AI Studio billing 활성화 또는 다음 날 재시도 |
| `2.1 기업현황` 섹션이 짧고 데이터 없음 | Gemini의 firecrawl_scrape 호출 실패. `<!-- firecrawl_status: failed -->` 확인 |
| `parse_llm_json` 실패 | trading role이 Gemini로 fallback된 경우 JSON 형식 차이. `cores/utils.py` 파서 보강 필요 |
| KIS API 401 | scenario 4는 KIS 호출 없음. 만약 발생하면 시나리오 5+ 코드가 우연히 호출된 것 |

## 4. 다음 세션을 위한 권장 작업 순서

1. **사용자에게 추가 자격증명 확인**: `~/env.local.yml`에 `firecrawl_api_key`와 (선택) `anthropic_api_key`, `krx_id`/`krx_pw` 추가됐는지
2. **의존성 설치 검증**: `python3.11 -c "import pandas, pykrx, playwright"` 등
3. **mcp_agent.config.yaml + secrets.yaml 작성**: 본 문서 §2의 옵션 A 또는 B
4. **`demo.py 005930` 첫 실행**: 로그를 `logs/subprocess/`에 자동 저장. 실패 시 어느 섹션에서 멈췄는지 grep
5. **결과 검토**:
   - `<!-- firecrawl_status: -->` 마커 → Gemini function calling 정상 작동 여부
   - 각 섹션 길이 → 어느 에이전트가 데이터 수집 실패했는지
   - PDF 정상 생성 여부 → playwright 정상
6. **이슈 발견 시**: PR로 fix. 본 문서 §3 "알려진 이슈" 참고

## 5. 이미 머지된 변경 (요약)

- `cores/llm/provider.py` (PR #5) — multi-provider resolver. role별 분기 + env override.
- 8개 호출 사이트 리팩토링 — `OpenAIAugmentedLLM` 하드코딩 제거
- `mcp_agent.config.yaml.example` `llm:` 섹션 + 3 예시
- `mcp_agent.secrets.yaml.example` google/grok placeholder
- `tests/test_llm_provider.py` 20 cases
- `docs/RUNBOOK_SCENARIO_4_LOCAL_ANALYSIS.md` 4.2 섹션 + API 키 조합표
- `docs/RELEASE_NOTES_v2.14.0.md` 신규
- `CLAUDE.md` v2.14.0 + Version History

본 문서를 갱신할 때:
- Level 5 실행 결과 추가
- Function calling 호환성 검증 결과
- 발견된 이슈와 PR 링크

---

**관련 문서**:
- [RELEASE_NOTES_v2.14.0.md](RELEASE_NOTES_v2.14.0.md) — multi-LLM-provider 설계
- [RUNBOOK_SCENARIO_4_LOCAL_ANALYSIS.md](RUNBOOK_SCENARIO_4_LOCAL_ANALYSIS.md) — 시나리오 4 운영 가이드
- [CLAUDE.md](../CLAUDE.md) — 전체 프로젝트 컨텍스트
