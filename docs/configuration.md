# Configuration

[简体中文](zh/configuration.md)

Wenyi reads `config.yaml` from the current working directory. If the file is missing, running the program creates a documented default configuration.

## Languages

```yaml
language:
  source: auto
  target: zh
```

`source: auto` asks the model to identify the source language. You may instead use an ISO 639-1 code such as `ja`, `en`, `ko`, `ru`, `fr`, `de`, or `es`. The current translation pipeline is primarily designed for Simplified Chinese output.

## Model provider

```yaml
llm:
  provider: deepseek
```

Selecting `deepseek` is enough for the built-in defaults:

- Base URL: `https://api.deepseek.com`
- API key environment variable: `DEEPSEEK_API_KEY`
- Strong tier: `deepseek-v4-pro`
- Cheap and fast tiers: `deepseek-v4-flash`

API keys are always read from environment variables so they are not accidentally committed with the configuration. Use `provider: fake` for offline tests that must not make network requests.

### Separate initial translation and refinement models

The top-level `translation_llm` block can assign body-text first drafts to a
separate provider. The main `llm` continues to handle Gemini refinement,
analysis, terminology, and other stages. `translation_llm` also handles only
the individual refinement segment when Gemini explicitly rejects it under a
content policy:

```yaml
llm:
  provider: agy
  command: agy
  tiers:
    strong:
      model: Gemini 3.5 Flash (Medium)

translation_llm:
  provider: openai-compatible
  base_url: https://token.sensenova.cn/v1
  api_key_env: SENSENOVA_API_KEY
  reasoning_style: deepseek
  tiers:
    strong:
      model: deepseek-v4-flash
      options:
        thinking: true
        reasoning_effort: high
```

With `pipeline.polish` enabled, each batch is first translated by the initial
provider and then refined by the main `llm` against the source text, draft,
position-visible narrative facts, relevant glossary, and recent finalized
translation. Whole-book/chapter future summaries are included only when
`future_context_policy: full` is selected.
Without `translation_llm`, first drafts continue to use the main `llm` for
backward compatibility. API keys remain environment-only.

### Separate refinement-recovery model

`polish_fallback_llm` is optional. When a main-model refinement response is
malformed, empty, misaligned, or fails at the provider boundary, Wenyi first
recursively splits the failed batch and retries the main model. Only a
single-segment leaf that still fails is sent to this recovery model. The
original draft remains pending if both models fail.

```yaml
polish_fallback_llm:
  provider: codex-cli
  command: codex
  cwd: C:/Users/you/AppData/Local/Temp/.agents/wenyi-codex-review
  timeout: 1200
  tiers:
    strong:
      model: gpt-5.6-sol
      options:
        reasoning_effort: high
```

Failure type, recursive range, and recovery-model indexes are persisted in the
batch event log. Explicit content-policy rejections still use the existing
context-stripping and `translation_llm` path first.

### Separate final-review model

`review_llm` optionally assigns only the independent final-review pass to a
different provider. Severe fixes still use the main `llm`, so the auditor finds
problems while the configured refinement model remains responsible for edits.
The local Codex CLI can be used as a read-only Sol auditor:

```yaml
review_llm:
  provider: codex-cli
  command: codex
  cwd: C:/Users/you/AppData/Local/Temp/.agents/wenyi-codex-review
  timeout: 1200
  tiers:
    cheap:
      model: gpt-5.6-sol
      options:
        reasoning_effort: high
```

This adapter launches an ephemeral `codex exec` process in a read-only sandbox,
sends the request through stdin, and explicitly forbids tools or file access.
It can serve final review or the rare failed refinement leaf without replacing
the primary translation/refinement models.

The first PDF import also reads `MINERU_API_KEY` to call the MinerU conversion service. This key is independent of the LLM provider and is not written to `config.yaml`.

Add the advanced fields only when you need a proxy, custom environment variable, timeout, retry policy, or model override:

```yaml
llm:
  provider: deepseek
  base_url: https://api.deepseek.com
  api_key_env: DEEPSEEK_API_KEY
  timeout: 600
  max_retries: 4
  tiers:
    strong:
      model: deepseek-v4-pro
      options:
        reasoning_effort: high
        thinking: true
    cheap:
      model: deepseek-v4-flash
      options:
        reasoning_effort: high
        thinking: true
    fast:
      model: deepseek-v4-flash
      options:
        thinking: false
```

Configured tiers override the corresponding provider defaults; omitted tiers continue to use their defaults. When a requested tier is unavailable, Wenyi follows the fallback chain `fast -> cheap -> strong`.

The selected provider owns and validates the contents of `options`. In the example above, `thinking` and `reasoning_effort` are DeepSeek-specific and do not belong to the common LLM interface.

### OpenAI and OpenRouter

OpenAI and OpenRouter have dedicated providers that select their own default Base URL, API key environment variable, request fields, and reasoning format. Their model tiers must be configured explicitly:

```yaml
llm:
  provider: openrouter
  tiers:
    strong:
      model: anthropic/claude-opus-4.6
      options:
        thinking: true
        reasoning_effort: high
    cheap:
      model: openai/gpt-5-mini
      options:
        thinking: true
        reasoning_effort: medium
    fast:
      model: google/gemini-3-flash
      options:
        thinking: false
```

The OpenAI provider reads `OPENAI_API_KEY`; OpenRouter reads `OPENROUTER_API_KEY`. Both providers allow `base_url` and `api_key_env` to override their defaults.

### Other OpenAI-compatible endpoints

Use `openai-compatible` for any endpoint implementing OpenAI Chat Completions:

```yaml
llm:
  provider: openai-compatible
  base_url: https://api.example.com/v1
  api_key_env: EXAMPLE_API_KEY
  # deepseek | openai | openrouter | none
  reasoning_style: deepseek
  tiers:
    strong:
      model: provider-model-name
      options:
        thinking: true
        reasoning_effort: high
        request_overrides:
          thinking:
            budget: 8192
```

`reasoning_style` converts the common `thinking` and `reasoning_effort` options into the request dialect accepted by the endpoint:

- `deepseek`: `thinking.type` plus `reasoning_effort`
- `openai`: `reasoning_effort`, with `none` sent when reasoning is disabled
- `openrouter`: `reasoning.effort`, with `reasoning.enabled: false` sent when disabled
- `none`: no conversion, for endpoints that rely on model defaults or custom request fields

`request_overrides` is an escape hatch for provider-specific fields that Wenyi does not know about. Its contents are merged recursively into the raw top-level request body after the selected reasoning dialect is generated. For example, an endpoint using `enable_thinking: true` can be configured as follows:

```yaml
llm:
  provider: openai-compatible
  base_url: https://api.example.com/v1
  reasoning_style: none
  tiers:
    strong:
      model: provider-model-name
      options:
        thinking: true
        request_overrides:
          enable_thinking: true
```

Choose a reasoning dialect according to the endpoint protocol, not the underlying model name. A relay serving a DeepSeek model should still use `reasoning_style: openai` when that relay expects OpenAI reasoning fields.

Local Ollama and vLLM endpoints are available through the `ollama` and `vllm` providers. Their default addresses are `http://localhost:11434/v1` and `http://localhost:8000/v1`, and neither requires an API key by default. Both require explicit model tiers. Ollama's OpenAI-compatible endpoint may use `reasoning_style: openai`; vLLM reasoning support depends on the model template and server arguments. When necessary, pass `enable_thinking` through `request_overrides.chat_template_kwargs`.

### Antigravity CLI (agy)

Use `provider: agy` to send each request to an installed and authenticated
Antigravity CLI in non-interactive print mode:

```yaml
llm:
  provider: agy
  # Optional executable path; defaults to agy from PATH.
  command: agy
  timeout: 600
  tiers:
    strong:
      model: Gemini 3.1 Pro (High)
    cheap:
      model: Gemini 3.5 Flash (Medium)
    fast:
      model: Gemini 3.5 Flash (Low)
```

Antigravity CLI does not expose a per-request system-prompt flag or a
native JSON response mode. Wenyi therefore labels system, user, and assistant
content and folds them into one ordinary `--print` prompt. JSON requests add a
plain-text output constraint and are parsed by Wenyi afterwards. Calls are
fresh, serialized, and run with `--mode plan`: Wenyi never passes `--continue`,
plan mode prevents translation prompts from requesting file-writing tools, and
concurrent pipeline stages wait for the prior agy process to finish to avoid
local state-file races.
The CLI also does not report token usage, so Wenyi's usage totals for this
provider are character-based estimates rather than billable token counts.
For compatibility with OpenClaw configurations, the short IDs
`gemini-3.1-pro[-low|-high]` and
`gemini-3.5-flash[-low|-medium|-high]` are sent as short IDs for agy 1.1. Wenyi
retries the short ID once after an explicit unknown-model error before falling
back to agy 1.0 display names, then caches the successful form. This tolerates
the brief model-registry race observed during agy 1.1 startup.

`cwd` may be set to choose the workspace visible to agy. Plan mode is not an
operating-system security boundary; use an OS sandbox or container when
untrusted prompts or stronger filesystem isolation are involved.

## Pipeline

```yaml
pipeline:
  review: false
  autofix_severe: false
  auto_resolve_glossary_conflicts: false
  polish: true
  backtranslate_sample: 0
  consistency_qa: false
  rolling_context_segments: 6
  book_understanding: true
  prescan_concurrency: 4
  review_concurrency: 4
  review_max_chars_per_batch: 0
  glossary_scope: chapter
  future_context_policy: current-only
  require_polish_success: true
```

- `review`: disabled by default; when enabled, automatically run the independent final-review stage after the complete book has been translated. The explicit `trans-novel review` command remains available while this is disabled.
- `autofix_severe`: during final review, retranslate severe omissions and mistranslations and adopt fixes that pass validation.
- `auto_resolve_glossary_conflicts`: before final review, let the main model choose final glossary translations from candidates and local source/translation context.
- `polish`: let the strong model refine each draft against the source and layered context. This may improve quality but significantly increases runtime and cost.
- `backtranslate_sample`: fraction of translated segments to inspect through backtranslation; `0` disables it.
- `consistency_qa`: run a final cross-chapter check of terminology, references, voice, and punctuation.
- `rolling_context_segments`: number of recent translated segments included with each translation batch.
- `book_understanding`: prescan the book to create chapter digests and a whole-book synopsis.
- `prescan_concurrency`: number of chapter-digest requests that may run concurrently.
- `review_concurrency`: number of contiguous final-review chunks that may run concurrently against the completed glossary; set it to `1` for sequential review.
- `review_max_chars_per_batch`: source-character budget for one final-review request. `0` uses three times the translation batch size; CLI auditors should use a larger value to amortize agent startup cost.
- `glossary_scope`: `chapter` includes terms relevant to the current chapter; `full` includes the complete glossary.
- `future_context_policy`: `current-only` (default) keeps full-book and full-chapter future plot summaries out of translation, polishing, and autofix prompts. `full` restores the legacy injection behaviour.
- `require_polish_success`: keep failed polish indexes pending and retry them on resume instead of silently treating the first draft as finished.

The command-line flags `--polish`, `--no-polish`, `--qa`, and `--no-qa` override the corresponding configuration values for that run.

Run final review independently with `trans-novel review INPUT`. `--force`
rechecks already reviewed translations, while `--fix` or `--no-fix` overrides
`autofix_severe` for that invocation. `--resolve-conflicts` and
`--no-resolve-conflicts` similarly override `auto_resolve_glossary_conflicts`.

## Output

```yaml
output:
  mono: true
  bilingual: false
  bilingual_order: target_first
  bilingual_preserve_source_style: false
  about_page: true
```

- `mono`: produce the monolingual Chinese edition as `<book-name>.zh.epub`.
- `bilingual`: produce a source-and-translation edition as `<book-name>.zh-bi.epub`.
- `bilingual_order`: `target_first` places the translation before the source; `source_first` reverses the order.
- `bilingual_preserve_source_style`: when `true`, source blocks inherit the book's normal text style instead of using the subdued gray style. This affects EPUB and HTML output only.
- `about_page`: append an “About this translation” project page to the book; set it to `false` to disable it.

Only the monolingual edition is enabled by default. `--bilingual` enables both editions, and configuration plus command-line switches can be combined to produce only the bilingual edition.

## Segmentation, honorifics, punctuation, and paths

```yaml
segment:
  max_chars_per_batch: 1800
  max_chars_per_segment: 1200

honorific:
  strategy: keep_style

punctuation:
  normalize: true
  quote_style: source

paths:
  state_dir: state
```

- `max_chars_per_batch`: approximate source-character budget for one model translation request.
- `max_chars_per_segment`: threshold for splitting an exceptionally long source paragraph.
- `honorific.strategy`: Japanese-source honorific policy: `keep_style`, `normalize`, or `drop`.
- `punctuation.normalize`: normalize Chinese sentence punctuation, ellipses, and dashes to full-width forms.
- `punctuation.quote_style`: `source` (default) follows the source typography, preserving `「」『』` in Japanese novels; `zh-cn` converts quotes to `“”‘’`.
- `state_dir`: location of checkpoints, chapter files, the glossary database, usage data, and reports.
