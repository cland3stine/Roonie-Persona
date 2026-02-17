## Persona & Dialogue Tuning (Detailed)
Last Reviewed (UTC): {{UTC}}

This section is the "how Roonie talks" spec, tied to specific code/config locations. It is intended to be sufficient to
reconstruct the current persona and conversation behavior if the repo is damaged.

### Persona Sources (Where Behavior Comes From)
1) Canonical YAML (minimal today)
- `D:\\ROONIE\\persona\\persona_policy.yaml`
  - Currently only encodes: `senses.enabled=false`, `local_only=true`, whitelist.
  - This file is loaded/validated during Control Room startup preflight:
    - `D:\\ROONIE\\src\\roonie\\control_room\\preflight.py` (`resolve_runtime_paths`, `_load_persona_policy`, `run_preflight`)
  - This YAML is also appended into the ProviderDirector prompt as a "do not violate" block:
    - `D:\\ROONIE\\src\\roonie\\provider_director.py` (`_persona_policy_path`, `_load_persona_policy_text`, `_build_prompt`)

2) Prompt Style Header (primary persona voice constraints)
- `D:\\ROONIE\\src\\roonie\\prompting.py`
  - `DEFAULT_STYLE` defines the core voice and safety rules that the model is instructed to follow.
  - `build_roonie_prompt(...)` constructs a deterministic prompt:
    - Header (DEFAULT_STYLE)
    - Channel + Viewer
    - Optional "Recent relevant context" block (see Context Carry-Forward)
    - Viewer message
    - "Roonie reply:" anchor

3) Behavior policy guidance (per-intent)
- `D:\\ROONIE\\src\\roonie\\behavior_spec.py`
  - `classify_behavior_category(...)` categorizes messages as:
    - `GREETING`, `BANTER`, `TRACK_ID`, Event categories, `OTHER`
  - `behavior_guidance(...)` adds explicit constraints per category, including:
    - Keep replies short/warm (1-2 sentences)
    - Prefer clean, professional language; slang occasional
    - No unsolicited commentary
    - Track-ID: no hallucinated track names; ask for timestamp/clip when now-playing unavailable
    - Events: brief thanks; OutputGate cooldown enforces anti-spam

4) ProviderDirector prompt composition (layering)
- `D:\\ROONIE\\src\\roonie\\provider_director.py`
  - ProviderDirector is the primary "brain" in live (default director).
  - Prompt layers are assembled in `_build_prompt(...)`:
    1. Base prompt from `build_roonie_prompt(...)`
    2. `behavior_guidance(...)` block
    3. Optional conversation continuity block (topic anchor) when continuity is indicated
    4. Optional library grounding block for music-intent questions (from local library index)
    5. Optional "Music facts policy" block for label/release-date questions (hedged answers if unverified)
    6. Optional "Memory hints" block (read-only cultural notes; keys-only allowlist; capped; PII-scrubbed)
    7. Canonical Persona Policy YAML appended (minimal today; senses hard-disabled)

### Current Persona Characteristics (As Implemented)
Source: `D:\\ROONIE\\src\\roonie\\prompting.py` + `D:\\ROONIE\\src\\roonie\\behavior_spec.py`
- Warm, restrained "regular in chat".
- Default brevity target: 1-2 sentences, short lines.
- "Classy/clean" language default most of the time; slang is allowed but should be occasional.
- Avoid assistant-y filler ("As an AI...", "How can I help...").
- Minimal punctuation intensity: 0-1 exclamation point typical.
- Emojis/emotes allowed but sparse; emote usage restricted to the approved list when configured.
- Safety: no personal info, no exact location; generalize location (example in prompt: "Washington DC area").

### Context Carry-Forward (Rolling Conversation)
1) Buffer rules
- `D:\\ROONIE\\src\\roonie\\context\\context_buffer.py`
  - Ring buffer, in-memory only, max turns: 3 (`ContextBuffer(max_turns=3)`).
  - Stores user turns only when relevant:
    - direct addressed, OR contains '?', OR starts with interrogative, OR category in utility set.
  - Stores Roonie turns only when:
    - they were actually sent, AND
    - they relate to a stored user turn.

2) Prompt injection
- `D:\\ROONIE\\src\\roonie\\prompting.py` (`_format_recent_context`)
  - Injects newest-first list of `user:` / `roonie:` lines, truncated to a small char cap.

Important current limitation:
- ProviderDirector does not yet have a robust "sent feedback" path to store assistant turns in its own context buffer
  because OutputGate is the final posting authority. Today, user-turn continuity is strong; assistant-turn continuity is
  weaker unless the message itself carries the reference (topic anchor / overlap / deictic followup).

### Topic Anchors (Dynamic, Natural Continuity Without "Stuck Topics")
Source: `D:\\ROONIE\\src\\roonie\\provider_director.py`
- Anchors are extracted conservatively from:
  - distinctive numeric phrases (e.g., "Maze 28"), and
  - conservative capitalized phrases (general topics).
- Anchors are short-lived (TTL in turns, currently 8).
- Anchors are only *used* when continuity is indicated:
  - music-intent, OR
  - deictic follow-up ("it/that/when?/which track"), OR
  - token overlap between current message and anchor.
- This avoids the prior "stuck topic" failure mode where an old music artist would get injected into unrelated banter.
- Library grounding is explicitly music-only (anchor kind tracked as `music` vs `general`).

### Music/Library Grounding + Best-Effort Music Facts
Sources:
- ProviderDirector: `D:\\ROONIE\\src\\roonie\\provider_director.py`
- Dashboard storage/index: `D:\\ROONIE\\src\\roonie\\dashboard_api\\storage.py`

Behavior:
- If the question is music-intent (track ID / library / label / release date), ProviderDirector may include:
  - "Library grounding (local)" block populated from `library_index.json` matches, to disambiguate "Artist - Title".
  - "Music facts policy" guidance:
    - If label/release date cannot be verified, answer best-effort but clearly hedge and ask for the exact title/link.

Library index path precedence (for grounding):
- `ROONIE_LIBRARY_INDEX_PATH` env (highest)
- `ROONIE_DASHBOARD_DATA_DIR\\library\\library_index.json`
- repo `D:\\ROONIE\\data\\library\\library_index.json` (fallback)

### OutputGate Controls That Affect Conversation (Not Persona, But User-Visible Behavior)
Source: `D:\\ROONIE\\responders\\output_gate.py` + dashboard runtime state
- Even if ProviderDirector proposes a reply, output can be suppressed by:
  - `ROONIE_OUTPUT_DISABLED=1` (Output Disabled)
  - `ROONIE_DRY_RUN=1` / `ROONIE_READ_ONLY_MODE=1` (DRY_RUN, no posting)
  - rate limiting (`ROONIE_OUTPUT_RATE_LIMIT_SECONDS`, live default set to 6s)
  - per-category cooldowns (greeting + event cooldown windows)
  - disallowed emotes (if approved list configured)
- Suppressions are logged with reasons (e.g., `OUTPUT_DISABLED`, `DRY_RUN`, `RATE_LIMIT`, `EVENT_COOLDOWN`).

### Operational Tuning Loop (Mental Note, Make This a Habit)
Goal: make "natural" a testable target and iterate deterministically.
1) Define an explicit rubric for "natural" (brevity, continuity, tone, no forced callbacks, no hallucinated facts).
2) Capture 10-20 real conversation chains from logs into fixtures.
3) Run them through dry `record_run.py` and live DRY_RUN mode and review line-by-line.
4) Tune context selection + prompt shaping in small steps and lock via regression tests.

### Open Tuning Items (Persona/Dialogue)
1) [TUNE-001] ProviderDirector assistant-turn carry-forward is limited (sent feedback path missing).
   Opened (UTC): 2026-02-17T00:17:10Z
   Last Reviewed (UTC): {{UTC}}
2) [TUNE-002] Prompt text has remnants of previous mojibake replacement (e.g., "1?2" instead of "1-2") which may confuse providers.
   Opened (UTC): 2026-02-17T00:17:10Z
   Last Reviewed (UTC): {{UTC}}
3) [TUNE-003] Tune banter tone further toward "classy/clean" ~70-80% while keeping occasional slang, without becoming robotic.
   Opened (UTC): 2026-02-17T00:17:10Z
   Last Reviewed (UTC): {{UTC}}
4) [TUNE-004] Improve music fact answers (labels/release dates) with stronger grounding when Rekordbox XML is available; keep hedging when unverified.
   Opened (UTC): 2026-02-17T00:17:10Z
   Last Reviewed (UTC): {{UTC}}
