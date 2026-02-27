# RPtuning - Runtime Personality and Behavior Spec for Roonie

Last full sync: 2026-02-27 07:37 AM ET (2026-02-27T12:37:59Z)
Synced to commit: `411d21d`
Repository root: `D:\ROONIE`
Obsidian canon: `D:\OBSIDIAN\AI Projects\ROONIE\03_PERSONA_AND_BEHAVIOR\PERSONA_CANON.md`

This is the runtime behavior specification for Roonie as of this sync.
If this file and source code disagree, source code wins.

## Source-of-Truth Priority (Recovery Safe)
1. Runtime code in `D:\ROONIE`
2. This file (`D:\ROONIE\RPtuning.md`)
3. Obsidian persona canon (`PERSONA_CANON.md`)
4. Decisions/session logs in Obsidian

## Primary Runtime Sources Audited
- `src/roonie/prompting.py`
- `src/roonie/provider_director.py`
- `src/roonie/behavior_spec.py`
- `src/roonie/language_rules.py`
- `src/roonie/safety_policy.py`
- `src/providers/router.py`
- `src/memory/injection.py`
- `responders/output_gate.py`
- `src/roonie/control_room/live_chat.py`
- `src/roonie/context/context_buffer.py`
- `persona/persona_policy.yaml`
- `data/inner_circle.json`
- `data/studio_profile.json`
- `data/senses_config.json`
- `data/trackr_config.json`
- `data/stream_schedule.json`
- `data/providers_config.json`
- `data/routing_config.json`

Validation sources used in this sync:
- `tests/test_conversation_continuation.py`
- `tests/test_continuation_live_scenarios.py`
- `tests/test_live_chat_retry_queue.py`
- `tests/test_memory_injection_phase20.py`

---

## 1) Latest Personality/Behavior Delta (Post-2026-02-23)

Behavior-affecting commits now reflected in this document:
- `66a15cc` - assistant-turn continuity is stored only after emitted and sent feedback
- `e71f09a` - conversation continuation detection + reply-parent hardening
- `2f40a05` - bang command detection (`!trackid`, `!id`, `!track`, `!previous`) with skill-toggle gate
- `dfbe830` - proactive favorites category and gating
- `8e014b2` - LLM "read the room" continuation layer with `[SKIP]` opt-out
- `411d21d` - continuation targeting refinement:
  - named-other targeting block (`TARGETING_OTHER_NAME`)
  - greeting-to-other block (`GREETING_OTHER_USER`)
  - mention-other block (`MENTION_OTHER_USER`)
  - reply-parent-to-other block (`REPLY_PARENT_OTHER`)
  - low-affinity OTHER block (`LOW_AFFINITY_OTHER`)

Net effect:
- Continuation is now balanced and bounded, with explicit hard negatives for other-directed chat.
- Direct-address detection is stricter (no possessive/third-person false positive steal).
- Traceability is higher (`continuation_reason`, `continuation_skipped`, `continuation_capped`, streak counters).

---

## 2) Core Identity and Voice Contract

Runtime voice contract from `DEFAULT_STYLE` plus behavior guidance:
- Roonie is a blue plushie cat in an underground/progressive house Twitch stream.
- Replies are short, natural, chat-like (typically 1-2 sentences).
- Always tag the viewer at the start (`@username`).
- Do not repeat names in-body as a habit.
- Avoid repetitive openers and repetitive bit loops.
- Do not end every message with a question.
- No em dash output.
- No Unicode emoji output.
- Not an assistant persona (`As an AI`, `How can I help` prohibited).
- Silence is valid when there is nothing useful to add.

Boundary contract:
- Respect everyone in chat.
- Do not weaponize Roonie for roasts on request.
- Teasing is allowed only in friendly family-style context and must stay warm.
- No fabricated memories, no hedged fabrication.
- No fabricated track/release/label metadata.
- No fabricated stream times/schedule claims.
- Roonie is well cared for; no neglected/unfed victim narrative.

---

## 3) Addressing and Trigger Model

### 3.1 Direct-address detection
A message is treated as addressed when any of these are true:
- `metadata.is_direct_mention == true`
- Explicit mention of Roonie aliases (`@roonie`, `@rooniethecat`, bot nick)
- Leading vocative (`Roonie ...`, `hey roonie ...`)
- Trailing vocative (`..., roonie?`)
- Named direct question/request (`Roonie how...`, `Roonie can you...`)

Important precision:
- Possessive third-person forms like `Roonie's ...` are explicitly not treated as direct address.

### 3.2 Trigger detection
For addressed messages, trigger is true when any are true:
- category is not `OTHER`
- message contains `?`
- message starts with direct verb (`fix`, `switch`, `change`, `do`, `tell`, `show`, `check`, `turn`, `mute`, `unmute`, `refresh`, `restart`, `help`)
- message length <= 3

### 3.3 Short-ack promotion
Direct-addressed `OTHER` status updates can be promoted to `BANTER` for a short ack when:
- no `?`
- non-empty meaningful text after leading mention strip
- <= 220 chars
- not a tiny low-substance fragment

---

## 4) Continuation Model (Balanced Mode)

Continuation is evaluated only when message is not directly addressed.

### 4.1 Eligibility gate
A message is eligible continuation only if:
- same viewer as the user turn immediately preceding the most recent sent Roonie turn
- recency gate passes: <= 3 user messages since that last Roonie turn

If no recent thread: `continuation_reason = NO_RECENT_THREAD`.

### 4.2 Hard-negative continuation blocks
Even if eligible, continuation is blocked when any of these are true:
- `REPLY_PARENT_OTHER` - Twitch reply parent points to someone other than Roonie
- `MENTION_OTHER_USER` - message @mentions another user handle
- `GREETING_OTHER_USER` - greeting targets another person
- `TARGETING_OTHER_NAME` - vocative targeting of configured non-Roonie names
- `LOW_AFFINITY_OTHER` - category `OTHER` with no continuity cues

Named-target configuration:
- default: `art, jen`
- env override: `ROONIE_CONTINUATION_OTHER_NAME_TARGETS`

### 4.3 Continuation cues used for OTHER messages
Used to avoid low-affinity continuation:
- track-id category
- question mark
- starts with direct verb
- deictic follow-up (`that one`, `which one`, `when?`, etc.)
- second-person cues (`you`, `your`)
- music-chat cues (`track`, `mix`, `drop`, etc.)
- overlap with current topic anchor

### 4.4 LLM continuation awareness layer
When continuation is allowed, prompt includes explicit "read the room" instructions and can return `[SKIP]`.
- `[SKIP]` is interpreted as NOOP only for continuation paths.
- `[SKIP]` is not parsed as control output for direct-address messages.

### 4.5 Safety cap and streak
- streak tracked per viewer
- after 4 consecutive continuation responses, next continuation is capped (`CAPPED`) and NOOPs
- direct address from that viewer resets streak
- `[SKIP]` does not increment streak

### 4.6 Why this solves recent latch problems
This design separates:
- "same viewer recently" from
- "message is still for Roonie"

Continuation can survive natural follow-ups, but hard negatives prevent butt-ins when viewer pivots to someone else.

---

## 5) Prompt Assembly Pipeline

Prompt build order in runtime:
1. Base prompt from `build_roonie_prompt(...)`
2. Behavior block from `behavior_guidance(...)`
3. Optional library grounding block
4. Optional music-facts hedge block
5. Optional memory hints block
6. Optional safety block (`refuse` / `sensitive_no_followup`)
7. Optional continuation awareness block
8. Optional canonical persona policy text append

Context budget used in prompt:
- max context turns: 8
- max context chars: 1200

Persona policy note:
- `persona/persona_policy.yaml` is loaded, but current file is config-only YAML (no prose), so no additional persona prose is appended to prompt right now.

---

## 6) Behavior Classification and Cooldowns

### 6.1 Categories
- `GREETING`
- `BANTER`
- `TRACK_ID`
- `EVENT_FOLLOW`
- `EVENT_SUB`
- `EVENT_CHEER`
- `EVENT_RAID`
- `PROACTIVE_FAVORITE`
- `OTHER`

### 6.2 Category detection highlights
- Event metadata maps directly to event categories.
- Bang track commands classify as `TRACK_ID`.
- Track-ID natural language regex also maps to `TRACK_ID`.
- Pure greetings map to `GREETING`.
- Question mark or short message (<= 80 chars) maps to `BANTER`.
- Else `OTHER`.

### 6.3 Cooldowns
- `EVENT_FOLLOW`: 45s
- `EVENT_SUB`: 20s
- `EVENT_CHEER`: 20s
- `EVENT_RAID`: 30s
- `GREETING`: 15s
- `PROACTIVE_FAVORITE`: 120s

---

## 7) Music, TRACKR, and Command Behavior

Metadata injected from live path:
- `now_playing`
- `track_enrichment` (label/year/style/genres)
- `previous_track` (+ enrichment when available)
- `stream_schedule`
- `inner_circle`
- `approved_emotes`
- `track_id_skill_enabled`

Track command behavior:
- supported: `!trackid`, `!id`, `!track`, `!previous`
- when `track_id_skill_enabled=false`: command NOOPs so Streamer.bot handles
- when `track_id_skill_enabled=true`: forced evaluate path, treated as addressed

Proactive favorite behavior:
- category `PROACTIVE_FAVORITE`
- brief natural shoutout guidance
- cooldown: 120s
- gated by TRACKR config flags/thresholds

---

## 8) Routing, Providers, and Moderation

Supported providers:
- `openai`
- `grok`
- `anthropic`

Current model defaults:
- OpenAI: `gpt-5.2`
- Grok: `grok-4-1-fast-reasoning`
- Anthropic: `claude-opus-4-6`

Routing modes:
- `active_provider`
- `random_approved`
- `weighted_random`

Current routing config (`data/routing_config.json`):
- `enabled=true`
- `general_route_mode=weighted_random`
- default weights: `grok 50`, `openai 25`, `anthropic 25`
- `music_route_provider=grok`

Moderation behavior:
- Non-OpenAI outputs (Grok/Anthropic) are moderated by OpenAI Moderation API (`omni-moderation-latest`).
- If moderation flags output: proposal is blocked (`MODERATION_BLOCK`).
- If moderation API fails: fail-open (`moderation_api_error=true`, output allowed).

---

## 9) Safety Policy

Classifier outputs:
- `allowed`
- `refuse`
- `sensitive_no_followup`

Refuse triggers include requests for:
- addresses
- phone numbers
- legal/full names
- email addresses
- doxxing terms
- IP info

Sensitive-no-followup triggers include:
- depression/suicidal/self-harm phrases

Safety behavior in prompt:
- `refuse`: in-character deflection, no identifying disclosure
- `sensitive_no_followup`: brief warmth, no probing follow-up

---

## 10) Memory Injection Rules

Source:
- SQLite `cultural_notes` table from `memory.sqlite`

DB path resolution order:
1. `ROONIE_MEMORY_DB_PATH`
2. `ROONIE_DASHBOARD_DATA_DIR/memory.sqlite`
3. `data/memory.sqlite`

Allowed tags/keys:
- `tone_preferences`
- `stream_norms`
- `approved_phrases`
- `do_not_do`
- `personality`
- `lore`
- `temp`

Filtering and limits:
- entries with PII/token/secret patterns are dropped
- `temp` entries respect `ttl_hours` expiration
- deterministic char/item caps applied

Important implementation detail:
- `memory.injection.get_safe_injection()` defaults are `max_chars=900`, `max_items=10`
- `ProviderDirector` overrides to `max_chars=2000`, `max_items=15` at runtime

---

## 11) Context Buffer and Continuity Persistence

Context buffer:
- bounded deque, `max_turns=12`
- prompt uses up to 8 most recent turns

User-turn storage:
- always stores user input turn with tags:
  - `direct_address`
  - `continuation`
  - `category`
  - `user`

Assistant-turn storage:
- response text is queued as pending
- assistant turn is persisted only when output feedback confirms both:
  - emitted == true
  - send_result.sent == true (when provided)

Session resets clear:
- context buffer
- topic anchor state
- pending assistant turns
- continuation streak map

---

## 12) Output Gate and Emote Suppression

Global output suppressors:
- `ROONIE_OUTPUT_DISABLED=1`
- `ROONIE_DRY_RUN=1` / `ROONIE_READ_ONLY_MODE=1`

Rate limits:
- global emit gap default 6s (`ROONIE_OUTPUT_RATE_LIMIT_SECONDS`)
- per-category cooldowns from behavior spec

`DISALLOWED_EMOTE` logic:
- normalize approved emote names
- scan output tokens
- ignore `@mentions` as emote candidates
- allow echoed tokens from viewer input
- suppress if token still emote-like and not approved

---

## 13) Observability and Debugging Signals

Director trace keys to monitor:
- `addressed_to_roonie`
- `trigger`
- `conversation_continuation`
- `continuation_reason`
- `continuation_capped`
- `continuation_skipped`
- `continuation_streak`
- `track_command`
- `track_id_skill_enabled`

Routing/proposal trace keys:
- `provider_selected`
- `model_selected`
- `provider_used`
- `moderation_provider_used`
- `moderation_result`
- `moderation_blocked_text`
- `provider_error_attempts`

Dashboard provider attribution path:
- storage prefers `trace.proposal.provider_used`
- fallback `trace.routing.provider_selected`
- then route-derived fallback

Continuation reason values seen in runtime/tests:
- `ADDRESSED`
- `ALLOW`
- `NO_RECENT_THREAD`
- `REPLY_PARENT_OTHER`
- `MENTION_OTHER_USER`
- `GREETING_OTHER_USER`
- `TARGETING_OTHER_NAME`
- `LOW_AFFINITY_OTHER`
- `CAPPED`

---

## 14) Runtime Data Snapshot (At Sync Time)

### 14.1 Inner circle (`data/inner_circle.json`)
- `@cland3stine` (Art, host)
- `@c0rcyra` (Jen, host)
- `@ruleofrune` (Art or Jen, stream account)
- `@fraggyxx` (friend)
- `@paranoidandroidz` (friend)

### 14.2 Studio profile (`data/studio_profile.json`)
- location display: `Washington DC area`
- emotes: `59 total` (`57 allowed`, `2 denied`)
- denied emotes: `RoonieWave`, `RoonieHi`

### 14.3 Senses config (`data/senses_config.json`)
- `enabled=false`
- `local_only=true`
- `never_initiate=true`
- `never_publicly_reference_detection=true`
- `no_viewer_recognition=true`
- whitelist: `Art`, `Jen`

### 14.4 TRACKR config (`data/trackr_config.json`)
- `enabled=true`
- `api_url=http://192.168.1.254:8755`
- `track_id_skill_enabled=false`
- `proactive_favorites_enabled=false`
- `proactive_play_threshold=3`
- `proactive_max_per_session=3`

### 14.5 Stream schedule (`data/stream_schedule.json`)
- timezone: `ET`
- Thursday `7:00 PM` (`Progressive House Set!`)
- Saturday `7:00 PM` (`Rule of Rune // Clandestine & Corcyra Live!`)

### 14.6 Provider config (`data/providers_config.json`)
- active provider: `openai`
- approved providers: `openai`, `grok`, `anthropic`

### 14.7 Routing config (`data/routing_config.json`)
- `general_route_mode=weighted_random`
- weights: `grok=50`, `openai=25`, `anthropic=25`

---

## 15) Verification Status (This Sync)

Executed at sync time:
```powershell
pytest -q tests/test_conversation_continuation.py tests/test_continuation_live_scenarios.py tests/test_live_chat_retry_queue.py
pytest -q tests/test_memory_injection_phase20.py
```

Results:
- continuation/live retry pack: `45 passed`
- memory injection pack: `6 passed`
- failures: `0`

---

## 16) Total-Loss Recovery Runbook (Personality + Behavior)

1. Restore repository to this commit lineage (or newer known-good).
2. Restore critical data files into `data/`:
   - `inner_circle.json`
   - `studio_profile.json`
   - `stream_schedule.json`
   - `trackr_config.json`
   - `providers_config.json`
   - `routing_config.json`
   - `memory.sqlite`
3. Restore required secrets/env for providers and moderation.
4. Validate continuation and memory behavior:
   ```powershell
   pytest -q tests/test_conversation_continuation.py tests/test_continuation_live_scenarios.py tests/test_live_chat_retry_queue.py
   pytest -q tests/test_memory_injection_phase20.py
   ```
5. In live logs/dashboard, confirm trace fields appear:
   - `continuation_reason`
   - `continuation_skipped`
   - `provider_used`
   - `moderation_result`
6. Run multi-user smoke prompts before going live:
   - direct mention to Roonie
   - same-user follow-up (continuation)
   - same-user pivot to named other (must NOOP)
   - same-user `@other_user` mention (must NOOP)
   - reply-parent to other user (must NOOP)

If any continuation misfire happens, debug order:
1. inspect `continuation_reason`
2. verify `reply_parent_user_login` metadata is present when replying
3. verify named-target env (`ROONIE_CONTINUATION_OTHER_NAME_TARGETS`)
4. verify direct-address matcher did not incorrectly classify as addressed

---

## 17) Known Caveats and Intentional Tradeoffs

- Continuation remains intentionally balanced, not strict-only:
  - ambiguous room-level follow-ups may still pass if they look like natural continuation.
- Moderation fail-open on API error is intentional for uptime; prompt/persona guardrails remain primary safety layer.
- `persona_policy.yaml` currently holds config, not behavioral prose, so it does not currently add extra canonical text to prompt.

---

## 18) Maintenance Rule

Any time personality/behavior logic changes in code, update in the same session:
- `D:\ROONIE\RPtuning.md`
- `D:\OBSIDIAN\AI Projects\ROONIE\03_PERSONA_AND_BEHAVIOR\PERSONA_CANON.md`
- `D:\OBSIDIAN\AI Projects\ROONIE\10_LOGS_AND_DECISIONS\DECISIONS.md`
- `D:\OBSIDIAN\AI Projects\ROONIE\10_LOGS_AND_DECISIONS\SESSION_LOG.md`

Do not treat this as aspirational docs. It is a runtime contract and disaster-recovery map.
