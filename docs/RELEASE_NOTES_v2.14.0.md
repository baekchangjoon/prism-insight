# PRISM-INSIGHT v2.14.0 — Multi-LLM-Provider Configuration

> **Release Date**: 2026-05-25
> **Branch**: `feat/multi-llm-provider` → `main`

## 개요

설정 파일 한 곳에서 LLM **provider + 모델을 role별로 지정**할 수 있게 되었습니다. OpenAI / Anthropic / Google Gemini / xAI Grok 중 골라 쓰거나 섞어 쓸 수 있고, 환경변수로 즉시 override도 가능합니다.

**기본값은 pre-v2.14 동작 그대로** — `llm:` 섹션을 추가하지 않으면 기존 배포는 같은 모델로 같은 결과를 냅니다. 회귀 위험 없음.

## 주요 변경사항

### 1. `cores/llm/provider.py` — 신규 LLM provider resolver

role 이름을 받아 (provider, 모델, AugmentedLLM 클래스)을 반환:

```python
from cores.llm.provider import LLMProvider, clean

# 사용 예시
llm_cls = LLMProvider.get_llm_class("analysis")
llm = await agent.attach_llm(llm_cls)
report = await llm.generate_str(
    message=msg,
    request_params=clean(RequestParams(
        model=LLMProvider.get_model("analysis"),
        reasoning_effort="none",  # 자동으로 non-openai에서는 strip됨
        maxTokens=32000,
    ), role="analysis"),
)
```

지원 provider:
| Provider | 모델 패키지 | 비고 |
|----------|------------|------|
| `openai` | `OpenAIAugmentedLLM` | 디폴트. trading role은 `OpenAIResponsesLLM`로 자동 라우팅 |
| `anthropic` | `AnthropicAugmentedLLM` | Claude Sonnet 4.6 등 |
| `google` | `GoogleAugmentedLLM` | Gemini 2.5 Flash/Pro (dragon1086/mcp-agent fork에 포함) |
| `grok` | `OpenAIAugmentedLLM` + base_url override | xAI는 OpenAI 호환 API. `base_url`을 secrets에 지정 |

### 2. role별 built-in 디폴트

| role | 디폴트 | 사용 위치 |
|------|--------|----------|
| `analysis` | openai · gpt-5.4-mini | KR/US 6개 분석 에이전트 |
| `summary` | openai · gpt-5.4-mini | Executive summary |
| `strategist` | anthropic · claude-sonnet-4-6 | Investment Strategist |
| `insight` | anthropic · claude-sonnet-4-6 | `/insight` 아카이브 Q&A |
| `trading` | openai · gpt-5.5 | Buy/Sell Specialist (OpenAI Responses API 사용) |
| `translator` | openai · gpt-5-nano | Telegram 번역기 + company name translator |

pre-v2.14에 코드 안에 하드코딩되어 있던 model 이름을 그대로 옮긴 것 — `llm:` 섹션 없이 그대로 배포하면 같은 모델 사용.

### 3. 설정 우선순위 (최상 → 최하)

1. `PRISM_LLM_PROVIDER_<ROLE>` / `PRISM_LLM_MODEL_<ROLE>` 환경변수 (role별 강제)
2. `PRISM_LLM_PROVIDER` / `PRISM_LLM_MODEL` 환경변수 (전 role 강제)
3. `mcp_agent.config.yaml` `llm.roles.<role>.{provider,model}`
4. `mcp_agent.config.yaml` `llm.default.{provider,model}`
5. role별 built-in 디폴트
6. 글로벌 fallback (`openai` / `gpt-5.4-mini`)

**Provider 변경 시 model 자동 보완**: operator가 `default.provider=google`만 설정하고 model을 지정하지 않으면 `_PROVIDER_DEFAULT_MODELS`에서 `gemini-2.5-flash` 자동 적용 (모델 이름이 provider 간 호환되지 않으므로 mix-up 방지).

### 4. `clean_request_params` — provider 미지원 파라미터 자동 strip

`reasoning_effort` (GPT-5 전용) 등을 non-openai provider에서 자동 제거. 호출 코드에서 일일이 provider 분기할 필요 없음:

```python
# 호출 코드는 이 한 줄만:
request_params = clean(RequestParams(
    model=..., reasoning_effort="none", maxTokens=...,
), role="analysis")
# provider=google이면 reasoning_effort가 자동으로 None으로 변경됨
```

### 5. `OpenAIResponsesLLM` 자동 fallback

매매 에이전트(Buy/Sell Specialist)는 `OpenAIResponsesLLM`로 OpenAI Responses API를 사용합니다. operator가 trading role의 provider를 google/anthropic으로 변경하면 자동으로 표준 chat-style AugmentedLLM으로 fallback (provider별 Responses API 동등물이 아직 없음).

```python
# trading role 호출 패턴
llm_cls = LLMProvider.get_llm_class("trading", prefer_responses_api=True)
# provider=openai → OpenAIResponsesLLM 반환
# provider=google → GoogleAugmentedLLM 반환 (Responses API 무시)
```

### 6. 리팩토링된 호출 사이트 (6개 파일)

이전: `from mcp_agent.workflows.llm.augmented_llm_openai import OpenAIAugmentedLLM` 하드코딩 → provider/model 변경 시 코드 수정 필요
이후: `LLMProvider.get_llm_class(role)` 단일 진입점

| 파일 | 변경 |
|------|------|
| `cores/report_generation.py` | 4개 사이트 (analysis × 2 + summary + strategist) |
| `cores/agents/telegram_translator_agent.py` | translator role |
| `cores/company_name_translator.py` | translator role |
| `cores/archive/insight_agent.py` | insight role (이미 Anthropic이었음, 라우팅만 통일) |
| `stock_tracking_agent.py` | trading role + `prefer_responses_api=True` |
| `stock_tracking_enhanced_agent.py` | trading role (KR sell decision) |
| `prism-us/us_stock_tracking_agent.py` | trading role × 2 (US buy + sell) |

### 7. `mcp_agent.config.yaml.example` 갱신

`llm:` 섹션 + 3가지 사용 예시 (default 유지 / 하이브리드 / 올-Gemini) + role별 설명 추가. `mcp_agent.secrets.yaml.example`에는 google/grok placeholder 추가.

### 8. CI 통합

`.github/workflows/ci.yml` approval-layer 잡에 `tests/test_llm_provider.py` 추가. 의존성에 `pyyaml` 추가. 커버리지 대상에 `cores.llm.provider` 포함.

## 일반적 사용 예

### 예 1 — 그대로 두기 (가장 흔한 케이스)

`mcp_agent.config.yaml`에 `llm:` 섹션 없이 그대로. 모든 role이 pre-v2.14 모델 사용.

### 예 2 — 분석은 Gemini, 전략은 Claude 유지

```yaml
llm:
  default:
    provider: google
    model: gemini-2.5-flash
  roles:
    strategist:
      provider: anthropic
      model: claude-sonnet-4-6
    insight:
      provider: anthropic
      model: claude-sonnet-4-6
    trading:
      provider: openai
      model: gpt-5.5
```

### 예 3 — 올-Gemini (Anthropic 키 없을 때)

```yaml
llm:
  default:
    provider: google
    model: gemini-2.5-flash
  roles:
    strategist:
      model: gemini-2.5-pro    # 통합 에이전트만 큰 모델
```

### 예 4 — 환경변수로 일회성 override

```bash
# 분석을 모두 anthropic으로 강제 (config 무시)
PRISM_LLM_PROVIDER=anthropic python demo.py 005930

# strategist만 새 모델 테스트
PRISM_LLM_MODEL_STRATEGIST=claude-opus-4-7 python demo.py 005930
```

## 실증 검증 (2026-05-25)

머지 후 Google Gemini API 키(`gemini_api_key`, `~/env.local.yml`)로 4단계 검증을 수행했습니다. 자세한 핸드오프: [HANDOFF_GEMINI_VERIFICATION.md](HANDOFF_GEMINI_VERIFICATION.md).

| Level | 검증 항목 | 결과 |
|-------|----------|------|
| 1 | `gemini-3.5-flash` REST API 직접 호출 | ✅ 정상 응답, usage tracking 정상 |
| 2 | `mcp_agent.workflows.llm.augmented_llm_google.GoogleAugmentedLLM` import | ✅ `google-genai` SDK 설치 후 성공 |
| 3 | `LLMProvider` env 우선순위 + `clean()` strip + trading `prefer_responses_api=True` fallback | ✅ 모든 동작 의도대로 |
| 4 | prism-insight Agent + `LLMProvider.get_llm_class("analysis")` → `GoogleAugmentedLLM.generate_str` (MCP 도구 없이) | ✅ Korean prompt → Korean 응답, 1.4초 |

**Level 5 (실제 `demo.py 005930`)는 추가 자격증명**(`firecrawl_api_key` + KRX 로그인) **필요로 보류** — 본 릴리스는 *플러그가능성*만 보장. 실제 함수 호출 호환성과 품질 동등성은 다음 세션에서 검증 예정.

확인된 가용 Gemini 모델 (이 키 기준 가장 위 5개):
- `gemini-3.5-flash` ⭐ (1M context, 권장 default)
- `gemini-flash-latest` (alias)
- `gemini-pro-latest` (strategist 후보)
- `gemini-3-pro-preview`, `gemini-3-flash-preview`

## 테스트

`tests/test_llm_provider.py` 신규 — 20 cases:
- 디폴트 role resolution
- role별 built-in 디폴트가 pre-v2.14 하드코딩 값과 일치
- YAML default vs YAML role vs env override 우선순위
- model 미지정 시 provider 디폴트 model 자동 적용
- 알 수 없는 provider → openai fallback + warning
- `clean_request_params`의 `reasoning_effort` strip 동작 (openai 보존, non-openai strip, None pass-through)
- 두 가지 end-to-end "user journey" (pre-v2.14 보존 + Gemini 하이브리드 swap)
- `resolve_llm()` shorthand
- `_KNOWN_PROVIDERS`와 `_PROVIDER_DEFAULT_MODELS` 동기화 invariant

전체 회귀: 74/74 pass (이전 54 + 신규 20).

## 알려진 제한사항

1. **Grok에 dedicated AugmentedLLM 없음**: dragon1086/mcp-agent fork에 `augmented_llm_grok`이 없어 OpenAI 호환 클래스 + `base_url` override 패턴 사용. xAI API가 OpenAI Chat Completions와 호환되므로 동작은 하지만 일부 OpenAI 전용 파라미터는 제거됨.
2. **`OpenAIResponsesLLM` 동등물 부재**: 매매 에이전트의 Responses API 사용은 OpenAI 전용. 다른 provider로 trading role을 변경하면 standard chat으로 자동 fallback되지만 JSON 응답 일관성이 떨어질 수 있음 → demo 모드에서 우선 검증 권장.
3. **Function calling 호환성**: 6개 분석 에이전트는 firecrawl/kospi_kosdaq 등 MCP 도구를 호출. Gemini 2.5는 OpenAI/Anthropic과 함수 호출 포맷이 다를 수 있어, swap 직후 첫 1-2건 보고서는 도구 호출 누락 가능. `<!-- firecrawl_status: -->` 마커(v2.13.1)로 모니터링 권장.
4. **품질 동등성 미검증**: provider별 분석 품질은 외부 LLM 출력 의존. 본 릴리스는 *교체 가능성*만 보장하며, *동등 품질*은 demo 모드에서 1-2주 운영하며 사용자가 직접 검증해야 함.

## 마이그레이션

스키마 변경 없음, requirements 변경 없음 (`pyyaml`은 mcp-agent가 이미 의존). 재시작만 하면 됩니다.

운영자 첫 점검:
```bash
# 1. 신규 설정 적용 없이 그대로 재시작 → 결과 동일해야 함
python demo.py 005930   # PDF 생성 + 모델 로그 확인

# 2. provider 변경 후 검증
PRISM_LLM_PROVIDER_ANALYSIS=google python demo.py 005930
# → "Completed price_volume_analysis - N characters" 로그에 모델명이 gemini-2.5-flash로 보이는지 확인

# 3. Telegram 메시지 비교 (동일 종목, 같은 시간대에 두 provider로 각각 실행)
```

## 텔레그램 공지

### 한국어

```
🔌 PRISM-INSIGHT v2.14.0 — Multi-LLM-Provider 설정

설정 파일 한 곳에서 LLM provider와 모델을 role별로 지정할 수
있게 되었습니다. OpenAI / Anthropic / Google Gemini / Grok 중
골라 쓰거나 섞어 쓸 수 있습니다.

📦 예시 — 분석은 Gemini, 전략 통합은 Claude:
  llm:
    default:
      provider: google
      model: gemini-2.5-flash
    roles:
      strategist:
        provider: anthropic
        model: claude-sonnet-4-6

🎯 6개 role 지원: analysis · summary · strategist · insight ·
trading · translator. 각 role 디폴트는 pre-v2.14 하드코딩 값과
동일하므로 llm: 섹션을 안 넣으면 결과 변화 없음 (회귀 위험 0).

🔧 환경변수로 즉시 override:
  PRISM_LLM_PROVIDER=google python demo.py 005930
  PRISM_LLM_PROVIDER_STRATEGIST=anthropic ...

📚 ChatGPT Plus 구독 없이 Google AI Studio 무료 tier로 운영 가능.
docs/RUNBOOK_SCENARIO_4_LOCAL_ANALYSIS.md 참조.
```

### English

```
🔌 PRISM-INSIGHT v2.14.0 — Multi-LLM-Provider Configuration

Pick the LLM provider + model per role from one config block.
OpenAI, Anthropic, Google Gemini, xAI Grok — use one or mix them.

📦 Example — Gemini for analysis, Claude for the strategist:
  llm:
    default:
      provider: google
      model: gemini-2.5-flash
    roles:
      strategist:
        provider: anthropic
        model: claude-sonnet-4-6

🎯 6 roles supported: analysis · summary · strategist · insight ·
trading · translator. Default per role matches the pre-v2.14
hardcoded model, so leaving the llm: block empty = zero behavior
change.

🔧 Env-var override for one-off runs:
  PRISM_LLM_PROVIDER=google python demo.py 005930
  PRISM_LLM_PROVIDER_STRATEGIST=anthropic ...

📚 You can run prism-insight without OpenAI now — just bring a
Google AI Studio key (free tier exists).
See docs/RUNBOOK_SCENARIO_4_LOCAL_ANALYSIS.md.
```

---

**Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>**
