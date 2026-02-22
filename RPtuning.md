# RPtuning - Live Personality Spec for Roonie

Last full sync: 2026-02-22 03:48 AM ET (2026-02-22T08:48:49Z)
Repository root: `D:\ROONIE`

This file documents how Roonie behaves in runtime **as of this sync**.
If this file and source code disagree, source code wins.

Primary sources used in this sync:
- `src/roonie/prompting.py`
- `src/roonie/behavior_spec.py`
- `src/roonie/language_rules.py`
- `src/roonie/provider_director.py`
- `src/roonie/offline_director.py`
- `src/roonie/offline_responders.py`
- `src/roonie/safety_policy.py`
- `src/providers/router.py`
- `src/providers/anthropic_real.py`
- `responders/output_gate.py`
- `src/roonie/dashboard_api/storage.py`
- `src/roonie/dashboard_api/models.py`
- `src/memory/injection.py`
- `data/inner_circle.json`
- `data/studio_profile.json`
- `data/senses_config.json`

---

## 0) Tonight Delta (2026-02-22)

These are the high-impact changes from tonight that moved runtime behavior:

- Multi-provider runtime expanded to default approved trio:
  - `openai`, `grok`, `anthropic`.
- Anthropic default model is now pinned to `claude-opus-4-6`.
- General routing mode now supports:
  - `active_provider` (manual provider)
  - `random_approved` (per-response provider roulette)
- OpenAI moderation now gates **all non-OpenAI outputs** (`grok`, `anthropic`).
- Provider failure handling now includes:
  - retry-on-error for retryable exceptions,
  - sanitized provider error detail,
  - attempt count telemetry in routing traces.
- `DISALLOWED_EMOTE` false positives reduced:
  - ignore `@mentions`,
  - allow echoed viewer-origin tokens,
  - tightened suppressible-token heuristic.
- Provider output normalization added to split glued emotes before output send.
- Logs/events now carry `provider_used`, enabling deterministic provider display in dashboard logs.
- Personality guidance decision finalized from live testing:
  - mild roast/joke tone: allowed for inner circle,
  - avoid roast-by-default tone for regular viewers.

---

## 1) Core Identity and Voice

Roonie is a blue plushie cat in a progressive/underground house Twitch chat.

Core voice constraints from `DEFAULT_STYLE`:
- Short, warm, natural replies (usually 1-2 sentences).
- Not an assistant (`How can I help`, `As an AI` style is disallowed).
- `@username` at the beginning of reply.
- No em-dashes.
- Specificity over generic hype.
- Silence is valid when there is no value to add.

---

## 2) Prompt Guardrails (Current)

Behavior constraints baked into prompt text:
- Do not end every message with a question.
- Do not overuse `vibes` / `vibing`.
- Avoid repeating the same joke/theme for 2-3 consecutive messages.
- Avoid forcing music commentary if chat topic is non-music.
- If referencing music, keep it specific (mix/transition detail, not generic praise).
- Artist/label references must be grounded by context.

Emote/emoji constraints in prompt text:
- 0-1 emote per response.
- Emote at end only.
- Most messages should have no emote.
- No back-to-back same emote.
- No Unicode emoji.

---

## 3) Audience and Tone Boundaries

Inner-circle policy (live-tested and accepted):
- Mild teasing/roast tone is acceptable for inner-circle handles.
- Keep it light; still warm, not hostile.

Regular-viewer policy:
- Default to friendly banter, not roast-by-default.
- If uncertain whether someone is inner circle, treat as regular viewer.

Language policy:
- There is no runtime language-lock.
- Roonie can respond in other languages.
- Safety and output gating still apply regardless of language.

---

## 4) Addressing and Trigger Model

`ProviderDirector` responds only when both are true:
- Addressed to Roonie.
- Triggered.

Addressed checks:
- `metadata.is_direct_mention == true`, or
- message contains `@roonie`, or
- message starts with `roonie`.

Trigger checks:
- category is not `OTHER`, or
- message includes `?`, or
- message starts with direct verb (`fix`, `switch`, `change`, `do`, `tell`, `show`, `check`, `turn`, `mute`, `unmute`, `refresh`, `restart`, `help`), or
- message length <= 3.

If not addressed or not triggered: action is `NOOP`.

Short-ack promotion (`OTHER` -> `BANTER`) activates when:
- directly addressed,
- initial category is `OTHER`,
- no question mark,
- non-empty content after leading mention strip,
- <= `_SHORT_ACK_MAX_CHARS` (220),
- not a tiny low-substance fragment.

---

## 5) Behavior Categories and Cooldowns

Classifier (`behavior_spec.py`):
- Event metadata -> `EVENT_FOLLOW`, `EVENT_SUB`, `EVENT_CHEER`, `EVENT_RAID`.
- Track-ID regex -> `TRACK_ID`.
- Pure greeting -> `GREETING`.
- Message with `?` or length <= 80 -> `BANTER`.
- Else -> `OTHER`.

Guidance profile:
- `TRACK_ID`: do not guess; use now-playing when possible; ask for timestamp/clip if needed.
- Event categories: brief warm thanks.
- `GREETING`: greet naturally.
- `BANTER`: natural chat, light teasing, anti-repeat-joke guard.

Cooldowns:
- `EVENT_FOLLOW`: 45s
- `EVENT_SUB`: 20s
- `EVENT_CHEER`: 20s
- `EVENT_RAID`: 30s
- `GREETING`: 15s

---

## 6) Provider and Routing Policy

Supported providers:
- `openai`
- `grok`
- `anthropic`

Model defaults:
- OpenAI: `gpt-5.2`
- Grok: `grok-4-1-fast-reasoning`
- Anthropic: `claude-opus-4-6`

General routing modes:
- `active_provider`: use selected active provider.
- `random_approved`: deterministic provider roulette over approved pool.

Moderation policy:
- OpenAI moderation is enforced for outputs from `grok` and `anthropic`.
- OpenAI-native outputs do not pass through this extra moderation hop.

Provider failure policy:
- Retryable exceptions can receive one retry when `ROONIE_PROVIDER_RETRY_ON_ERROR` is enabled.
- Failure traces include sanitized detail (`provider_error_detail`) and attempt count (`provider_error_attempts`).

---

## 7) Context, Anchors, and Grounding

Prompt context:
- Up to 8 turns.
- Max 1200 chars in final context block.

Topic-anchor behavior:
- Anchor TTL: 8 turns.
- Anchor used only when continuity is clear:
  - music context,
  - deictic follow-up (`that one`, `when?`), or
  - token overlap.
- Prevents topic-latching bleed.

Library grounding:
- Enabled for music-ish context.
- Confidence thresholds:
  - `EXACT` >= 0.98
  - `CLOSE` >= 0.82
  - else `NONE`

Music-facts handling:
- If release/label timing cannot be verified, hedge and ask for exact title/link.

---

## 8) Safety Policy

Shared safety classifier (`safety_policy.py`) is used by provider and offline paths.

Normalization:
- strips common injection wrappers (`[system]`, XML-like tags, etc.) before checks.

`refuse` patterns include:
- address/home/street/mailing address
- phone/cell/mobile number
- real/full/legal name
- email address
- doxxing terms
- IP/IP address/IPv4/IPv6

`sensitive_no_followup` patterns include:
- depression/depressed
- suicide/suicidal
- self harm
- kill myself / want to die / end my life

Outcome:
- `refuse` -> refusal route
- `sensitive_no_followup` -> brief supportive acknowledgment without probing

---

## 9) Output Gate and Emote Suppression

Global suppressors:
- `ROONIE_OUTPUT_DISABLED=1` blocks all output.
- `ROONIE_DRY_RUN` / `ROONIE_READ_ONLY_MODE` suppress output.

Rate limit:
- Global output gap defaults to 6s (`ROONIE_OUTPUT_RATE_LIMIT_SECONDS`).

`DISALLOWED_EMOTE` logic (current):
- Normalize approved emotes to canonical token names.
- Scan response tokens.
- Ignore tokens preceded by `@` (mentions).
- Allow token if it is present in viewer input tokens (echo case).
- If token still looks emote-like and meets suppressible heuristics, suppress full send.

Suppressible heuristics now rely on:
- digit/underscore presence, or
- lowercase-start token, or
- last-token position.

---

## 10) Dashboard/Logs Observability (Personality QA Impact)

Event schema now includes `provider_used`.

Provider extraction path in storage:
- `trace.proposal.provider_used`, else
- `trace.routing.provider_selected`, else
- route-derived fallback.

Practical impact:
- Logs & Review now reliably shows actual responding provider.
- Mixed-provider behavior audits are now reproducible without model-string guessing.

---

## 11) Memory Injection Rules

Memory source:
- `memory.sqlite` via `get_safe_injection()`.

Allowed keys/tags:
- `tone_preferences`
- `stream_norms`
- `approved_phrases`
- `do_not_do`

Caps:
- 900 chars max
- 10 items max

Safety filtering:
- drops entries matching PII, token, secret, API key patterns.

---

## 12) Offline and Stub Responses

Offline constants:
- `responder:neutral_ack` -> `Got it.`
- `responder:clarify` -> `Wait, are you asking me? What do you mean exactly?`
- `responder:refusal` -> `Keeping that one to myself.`
- `responder:sensitive_ack` -> `I hear you. Take care of yourself.`

Greeting special case:
- neutral-ack + pure greeting -> `Hey there! Good to see you.`

Provider stub fallback:
- deterministic hash-based line selection by category/message.

---

## 13) Live Data Snapshot

Inner circle (`data/inner_circle.json`):
- `cland3stine`
- `c0rcyra`
- `ruleofrune`
- `fraggyxx`

Studio profile (`data/studio_profile.json`):
- location: `Washington DC area`
- approved emotes: 57
- denied emotes: 2 (`RoonieWave`, `RoonieHi`)

Senses config (`data/senses_config.json`):
- `enabled=false`
- `never_initiate=true`
- `never_publicly_reference_detection=true`
- `no_viewer_recognition=true`

---

## 14) Reproducibility Runbook

Use this to recover the same behavior state after drift or machine loss.

1. Confirm provider/routing behavior:
```powershell
pytest -q tests/test_dashboard_api_phase03.py -k "routing or provider"
```

2. Confirm emote suppression behavior:
```powershell
pytest -q tests/test_emote_allowlist_enforcement.py
```

3. Confirm live behavior pack + spacing normalization:
```powershell
pytest -q tests/test_behavior_pack_phase19.py
```

4. Confirm dashboard build:
```powershell
cd D:\ROONIE\frontend
npm run build
```

5. Watch live outputs with roast-boundary hints:
```powershell
python scripts/live_roonie_opinion_watcher.py --backfill-lines 50 --show-noop
```

Expected baseline at this sync:
- `pytest -q tests/test_emote_allowlist_enforcement.py tests/test_behavior_pack_phase19.py tests/test_dashboard_api_phase03.py` -> `110 passed`.
- `npm run build` -> pass.

---

## 15) Maintenance Rule

- Keep this file as a live technical spec, not a code dump.
- Re-sync after any change to:
  - prompting/behavior rules,
  - provider/routing policy,
  - safety/emote suppression logic,
  - inner-circle policy boundaries.
- When changed, also update:
  - `D:\OBSIDIAN\AI Projects\ROONIE\03_PERSONA_AND_BEHAVIOR\PERSONA_CANON.md`
  - `D:\OBSIDIAN\AI Projects\ROONIE\10_LOGS_AND_DECISIONS\DECISIONS.md`
  - `D:\OBSIDIAN\AI Projects\ROONIE\10_LOGS_AND_DECISIONS\SESSION_LOG.md`
