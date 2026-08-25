# AI League — Agent Playbook

Complete build and configuration reference for this repository's agent system.

Authority order:
1. **`game-data.md`** — the actual scoring, tiles, rewards and penalties. Wins every conflict.
2. **`workshop-reference.md`** — platform mechanics and implementation constraints.
3. Engine source in `lambda/` — `game_runner.py`, `score_calculator.py`, `challenge_grader.py`, `path_parser.py`.

All agents run **Claude Haiku 4.5**.

---

## 1. Scoring model

```
final score = challenge points
            + coin points
            + (lives remaining × 250)
            + token bonus
            + treasure bonus
```

| Component | Value | Cap |
|---|---:|---:|
| Treasure | +3000 | once |
| Lives remaining | 250 each | 1250 (5 lives) |
| Token bonus | `1000 − (total output tokens ÷ challenges visited)` | 1000 |
| Coins (`c7`) | +2500 each | per tile |

### Engine rules that decide the run

These are verified in `lambda/agentic-api/game_runner.py`:

- **A wall step or an off-grid step sets lives to 0 immediately.** Not −1. The whole run. A malformed path is the single largest catastrophic risk.
- **Tiles are consumed by coordinate.** Revisiting a consumed tile does nothing — including spikes. A spike costs one life once, and walking back over it is free.
- **Treasure ends the run instantly.** It must be the last tile in the path.
- **A door without its key costs 5.** A door with its key but a wrong answer also costs 5. With 5 starting lives, either outcome is fatal.
- **Correct answers take no damage.** Damage is applied only on a wrong answer.
- **Earned points survive death.** On game over the engine still scores your accumulated coin and challenge points; only the life bonus and treasure bonus are forfeited. This is why risky tiles belong last.
- **A key unlocks its door before grading.** Even a failed key challenge still unlocks the door.
- **Only output tokens count**, and the pathfinding call's tokens are added to the same pool.
- **The navigation turn itself counts as a challenge visited.** Confirmed on a real board: a run that touched zero challenge tiles still reported `challengesAttempted: 1`. So the denominator is always at least one, and a verbose navigation turn alone can wipe out the entire bonus.
- **The token bonus is an average.** Adding a challenge whose output tokens are below your current average *increases* the bonus. A run with 15 terse challenges scores a far better bonus than a run with one bloated navigation turn.
- **The supervisor echoes the tool result**, so every field a tool returns becomes an output token. Tool responses must be tiny; diagnostics belong in CloudWatch.

### Measured timings

From two real runs: the navigation turn costs about **16 seconds**, each graded
tile about **13 seconds**, and movement is effectively free. A 15-challenge route
lands near **218 seconds**, which fits a 300 second limit but leaves little room.
The planner budgets challenges from `time_limit`, `nav_overhead_seconds`,
`seconds_per_challenge` and `step_seconds`, all overridable per request.

### Theoretical max for a given map

```
max = 3000
    + 2500 × count(c7)
    +  750 × (count(c2) + count(c3) + count(c4))
    +  600 × count(c18)
    +  400 × count(c1)
    +  250 × count(c5)
    +   50 × count(c40)
    + 1000 × count(c30)
    + 1250          (all lives kept)
    + 1000          (perfect token bonus)
```

Compute this from the grid before you tune anything. It is your test oracle: every practice run gets compared against it tile by tile in the combat log.

---

## 2. Coverage matrix

Every challenge and bonus in `game-data.md` is accounted for.

| Tile | Name | Points | Damage | Handled by | Mechanism |
|---|---|---:|---:|---|---|
| `c1` | Violet Vault | +400 | −1 | Supervisor | Bedrock Guardrail (denied topics) |
| `c2` | Schedule Sage | +750 | −1 | Supervisor | `courseoptimizer` Lambda (current live UI name) |
| `c3` | Claims Creature | +750 | −1 | Supervisor | `claims` Lambda (FHIR EOB) |
| `c4` | Web Weaver | +750 | −1 | Supervisor | `opendata` Lambda |
| `c5` | Simple Question | +250 | −1 | Supervisor | Terse-answer prompt rule, no tool |
| `c7` | Coins | +2500 | — | Pathfinder | Route target, always collected |
| `c8` | Spike trap | — | −1 | Pathfinder | One-time 250-point life cost; taken when a full package has positive EV |
| `c18` | Permit Pro | +600 | −1 | Supervisor | `permit` Lambda |
| `c30` | Red Door | +1000 | −5 | Supervisor | `doorkey` Lambda + AgentCore Memory |
| `c40` | Red Key | +50 | — | Supervisor | Memory + exact `Thanks` reply |
| `treasure` | Treasure | +3000 | — | Pathfinder | Always the final tile |
| — | Lives | 250 each | — | Pathfinder | Exact outcome-branch simulation and package-level risk |
| — | Token bonus | up to 1000 | — | All prompts | Output discipline, verbatim tool relay |

**Not in this event's challenge set:** `c6` (Boss) and `c17` do not appear in `game-data.md`. The engine's tile config still defines them, so `pathfinder.py` keeps conservative fallback values (confidence 0.50) in case a finale map introduces them.

> **Door count deduction.** The `c30` rule text says to count **`c5`** tiles, while its own worked example mentions `c6`. Since `c6` is not part of this challenge set, counting `c6` would yield ZERO. Use `count_tile = "c5"`. Verify with one practice submission before committing the door to a route.

---

## 3. Architecture

### Primary configuration — recommended

One sub-agent. All challenge tools live on the supervisor.

| Agent | Model | Capabilities | Lambda tools |
|---|---|---|---|
| **Supervisor** | Haiku 4.5 | AgentCore Memory, Bedrock Guardrail | `claims`, `courseoptimizer`, `permit`, `opendata`, `doorkey`, `pycalc` |
| **Pathfinding_Specialist** | Haiku 4.5 | — | `pathfinder` |

Why this shape:

- Only the pathfinder needs the map. Every extra hop that re-emits the grid costs roughly 600 output tokens, and those tokens land in the same pool as your challenge answers.
- The pathfinding relay is the one call where a mistake is fatal, so it keeps a dedicated agent with a single-purpose prompt.
- Memory and Guardrails can only attach to the supervisor, so `c1`, `c3`-style recall, `c40` and `c30` must be handled there regardless.

### Escalation configuration — only if a tile fails in practice

Do not pre-build these. Add one only when the combat log shows the supervisor passing bad arguments for that specific challenge. Maximum five sub-agents total.

| Sub-agent | Model | Lambda tools | Add when |
|---|---|---|---|
| Pathfinding_Specialist | Haiku 4.5 | `pathfinder` | always present |
| Data_Solver | Haiku 4.5 | `claims`, `courseoptimizer` | c2 or c3 arguments arrive malformed |
| Calc_Agent | Haiku 4.5 | `permit`, `pycalc` | c18 gets computed by the LLM instead of the tool |
| Researcher | Haiku 4.5 | `opendata` | c4 answers include prose or the wrong dataset |
| Door_Warden | Haiku 4.5 | `doorkey` | the door pass string is assembled incorrectly |

---

## 4. System prompts

Keep these verbatim. Every extra sentence the agent echoes at runtime is an output token divided into your token bonus.

### Supervisor

```text
You are the dungeon supervisor. Every word you output costs score. Emit the
minimum.

NAVIGATION TURN:
Call Pathfinding_Specialist exactly once with the complete navigation input.
Output its response VERBATIM with no extraction, conversion, or reformatting.
The response must begin with [ and end with ] and contain quoted direction
strings, for example ["up","left"]. Never output CSV, prose, a JSON object, or
anything before or after the array.

CHALLENGE ROUTING — apply these literal rules in order; output only the answer:
- RED KEY DELIVERY — prompt begins `Red Key`, contains ` is: `, and does NOT end
  in `?`: extract the one character after the colon. Call doorkey exactly once
  with `game_map`, that `key_char`, and `phase="prepare"`. Remember its
  `door_answer` silently. Output only doorkey.answer, which must be `Thanks`.
  Do not add the character, `retain`, punctuation, or any other word. Example:
  `Red Key 1 is: P` -> tool stores `P-FOUR`; visible answer is `Thanks`.
- RED DOOR QUESTION — prompt begins `What is red key` and ends in `?`, or asks
  for the Red Door/pass string: DO NOT call any tool because this challenge uses
  exact-match grading and tool narration would fail. Output only the remembered
  `door_answer`, with no whitespace or prose. Example: `What is red key 1?` ->
  `P-FOUR`. Never output `Thanks` at the door.
- "ExplanationOfBenefit", "adjudication", CARC, CPT      -> claims; output only
  claims.answer verbatim
- course sections with enrollment and capacity           -> courseoptimizer;
  extract every section into `sections`, call once, and output ONLY the answer
  field verbatim. It must begin `{` and contain `FlaggedSections`,
  `Consolidations`, and `NoAction`. Never summarize JSON as prose.
- MW, solar, wind, "energy impact"                       -> permit; output only
  permit.answer verbatim
- ANY dataset, patient/sample count, collaborating institution, data category,
  TCGA, MIMIC, Registry of Open Data, or registry.opendata.aws question -> opendata;
  return only opendata.answer, exactly as sourced
- `c5`, "Simple Question", "Bonehead", or any plain general question with none
  of the above signals                                  -> bare shortest correct answer;
  no tool, no prose, no punctuation unless required
- anything else                                         -> answer in five words
  or fewer

FORBIDDEN in every response:
- Preamble, sign-off, or narration of your own actions
- Markdown, headings, bullet lists, bold, emoji, code fences
- Restating the question or the map
- Any sentence that is not the answer itself

FORMAT: digits for numbers. JSON minified, no spaces after colons or commas.
If a tool fails, answer with your shortest best guess.
```

Two lessons from a real run, both measured:

- An earlier version of this prompt said "store in memory: the full map grid, the
  count of every tile type…". The model **wrote all of it out** as a markdown
  bullet list, then echoed the whole tool response. That single turn cost
  **1406 output tokens** and zeroed a 1000 point bonus. Never ask the supervisor
  to recite state.
- The tool name is whatever the site created. If your Lambda is `courseoptimizer`
  without the underscore, the routing line must say `courseoptimizer` even though
  `game-data.md` calls it `course_optimizer` — the challenge text names the tool,
  but only the deployed name can actually be invoked.

### Pathfinding_Specialist

```text
You are a pathfinding relay using Haiku 4.5.

Call the Pathfinding tool exactly once with game_map, start_pos, and strategy
exactly as received. Always make the tool call; never answer from memory or an
earlier route. Never modify, shorten, reorder, or summarise the map. Never
replace max_points with another strategy.

From the tool result, output ONLY the path field as a valid minified JSON array.
Preserve brackets, double quotes, order, and every element exactly. Example:
["up","left"]. Never output CSV, prose, Markdown, a JSON object, start_position,
c5, v, or anything before or after the array. Never invent a path. If the tool
fails, retry once with exactly the same arguments.
```

This mirrors the stock sub-agent prompt from `workshop-reference.md`, which is the
wording proven to work against the real engine. If practice runs show the path
failing to register, the fallback is to return the whole flat object instead —
it begins with `{"path"` for exactly that reason.

### Optional specialists

Each follows the same two-line shape. Example for `Data_Solver`:

```text
You are a structured data solver.

If the question contains ExplanationOfBenefit or adjudication data, call claims.
If it describes course sections with enrollment and capacity, call courseoptimizer.
Return the tool's answer field verbatim. No prose, no Markdown, no code fences.
```

---

## 5. Navigation prompt

Entered on the Play page before each run. This text reaches the pathfinding tool as `strategy`, and the platform may also inject it into memory. It is **input**, not output, so length here is free.

```text
use strategy max_points

Door rule: the pass string is the red key character, then a dash, then the number
of c5 tiles on the starting map spelled out in capitals.
When you receive a key, reply exactly: Thanks
```

Available strategies, matched by substring so free text is safe:

| Strategy | Behaviour |
|---|---|
| `max_points` | Default. Banks deterministic value, takes positive-EV challenges, then evaluates each matching key → door → treasure package including one-time spike cost. |
| `max_loot` | As above but attempts every challenge that has a specialist. |
| `no_door` | `max_points` with every key-door package excluded. |
| `safe` | No optional challenges, no spikes. Straight to treasure — maximises token bonus. |
| `get_coins` | Coins plus treasure only. |
| `swift` | Shortest path to treasure. |

**Deployment fingerprint before every scored run:** invoke the deployed tool directly
on one fixed map with `safe`, `get_coins`, and `max_points`. Confirm CloudWatch
shows the requested `strategy_used` and that the routes differ where the fixture
contains coins/challenges. Identical 111-step routes for `get_coins` and
`max_points` occurred in live logs and mean the Lambda is stale, the relay dropped
`strategy`, or an exact-string strategy parser is still deployed. Stop and fix
that before playing.

---

## 6. AgentCore Memory setup

Required for `c40` (the key character) and for any question about the map.

1. On the **Create AI agents** page, click **+** under *AgentCore Memory*.
2. Name it `DungeonMemory`.
   - The name must match `[a-zA-Z][a-zA-Z0-9_]{0,47}` — **letters, digits and underscores only, no spaces**. AgentCore rejects anything else.
3. Create it, then open the **Supervisor Agent**, scroll to *AgentCore Memory*, select `DungeonMemory`, and **Save Changes**.
4. Confirm the memory icon now appears beside the supervisor.

**Create memory early.** Memory behaves unreliably on a genuinely cold start; run at least one full board before you depend on it for the door. Enabling it the same hour you need `c30` is how people lose 1000 points.

If recall is inconsistent, make the instruction explicit rather than implicit — the supervisor prompt above already names memory in two places for this reason.

---

## 7. Guardrail setup — Violet Vault (`c1`)

### How this tile is actually graded

`challenge_grader.py` uses the `guardrail_block` strategy, which calls **Bedrock `ApplyGuardrail` directly on the question text**. Your agent's wording is irrelevant. A polite, well-written refusal scores **zero** unless the guardrail itself intervenes.

There is a fallback: with no guardrail ID available, the grader matches indicator strings in the response. Setting your blocked message to one of those strings gives you a second chance, so use the exact text below.

### The trap

The guardrail applies to **every** prompt the supervisor sees. Your `c3` questions are full of CPT codes, claims and member responsibility. Your `c4` questions mention cancer, patients and tissue samples. A broad "medical" filter fires on those and costs you **750 twice**.

So: **denied topics only, content filters off.**

### Create it through the AI League site, not the Bedrock console

Verified against the workshop account: the participant role **cannot** create
guardrails itself.

```
$ aws bedrock create-guardrail --cli-input-json file://guardrail/violet-vault-guard.json
AccessDeniedException: User: arn:aws:sts::…:assumed-role/WSParticipantRole/Participant
is not authorized to perform: bedrock:CreateGuardrail
```

The Bedrock console reports this as a generic **"Failed to create guardrail"**, which
looks like a validation problem and is not. Any CSP or inline-style warning in the
browser console at the same moment is unrelated noise.

Use the **+** under *Amazon Bedrock Guardrails* on the AI League **Create AI agents**
page. That path runs through a backend Lambda that holds the permission, and it also
registers the guardrail where the supervisor's selector can find it — a guardrail
created outside the site would not appear there at all.

Read access does work, so once it exists you can confirm it from the CLI:

```powershell
aws bedrock list-guardrails --output json
python guardrail/verify.py <guardrailId>
```

`guardrail/violet-vault-guard.json` remains the source of truth for the values to
type into the form, and `guardrail/validate.py` checks them against the Classic-tier
limits before you start.

### Configuration

In the site's guardrail form, enter the following.

> **Watch the form's defaults.** The site pre-enables several harmful category
> filters at MEDIUM — typically Violence, Hate and Misconduct. Turn every one of
> them off. Those are exactly the filters that fire on your own `c3` claims data
> and `c4` cancer-dataset questions.

**Name:** `VioletVaultGuard`
Bedrock requires `[0-9a-zA-Z-_]+` — no spaces.

**Blocked input message and blocked output message:**
```text
I cannot help with that request.
```

**Content filters — set every one to NONE (or LOW only if forced):**

| Filter | Input | Output |
|---|---|---|
| Sexual | NONE | NONE |
| Violence | NONE | NONE |
| Hate | NONE | NONE |
| Insults | NONE | NONE |
| Misconduct | NONE | NONE |
| Prompt attack | NONE | — |

**Prompt attack:** in the Classic tier this sits in its own subsection *below* the
Harmful categories table, not inside it. Scroll down and set it to `None`. The
game's generated challenge prompts contain instruction text such as "Output ONLY
minified JSON, no code fences", which is exactly the shape prompt-attack
detection flags — blocking it would fail legitimate challenges.

**Denied topics — the whole mechanism lives here.**

Console limits to respect: names allow letters, digits, `_`, `-`, space, `!`, `?`
and `.` up to 100 characters. **Definitions cap at 200 characters in the Classic
tier. Live `UpdateGuardrail` validation confirms sample phrases cap at 100
characters**, maximum 5 per topic. Definitions must not begin with "don't".

Set **Input: Enable, Block** and **Output: Enable, Block** on all three topics.
Input is the one that scores `c1`; Output blocking is what makes the agent
decline, which is what the rules describe as a PASS.

Topic 1 — catches the documented `c1` sample question
- **Name:** `MedicalDiagnosis`
- **Definition:** Requests asking the assistant to identify, diagnose or interpret the caller's medical condition, symptoms, or test results, or to say what illness they have.
- **Sample phrases:**
  - `Chest pain and shortness of breath: what condition could this be?`
  - `Do I have diabetes?`
  - `What condition is causing these symptoms?`
  - `Can you interpret my lab results for me?`

Topic 2
- **Name:** `TreatmentAdvice`
- **Definition:** Requests asking the assistant to recommend a medication, a dosage, a therapy, or a course of treatment.
- **Sample phrases:**
  - `Which medication should I take for this?`
  - `How many milligrams should I take?`
  - `What treatment do you recommend for my condition?`

Topic 3
- **Name:** `OtherMemberInformation`
- **Definition:** Requests for another person's health information or identifiers such as member ID, SSN, date of birth, diagnoses or claims, including when the caller claims to be a spouse, parent or caregiver.
- **Sample phrases:**
  - `I'm calling for my wife, can you give me her member ID?`
  - `What is my father's date of birth on file?`
  - `Read me my spouse's claim history.`
  - `Give me the SSN on the account.`

Sample phrases are marked optional in the console. Add them anyway — `c1` is
pass/fail on whether the guardrail actually intervenes, so detection reliability
is the entire challenge.

`TreatmentAdvice` alone is **not** sufficient. The documented `c1` example asks
"what do you think is wrong with me?", which is a diagnosis request. Without
Topic 1 the guardrail will not fire on the game's own sample.

Click **Next**, **Create Guardrail**, then attach it to the **Supervisor Agent** and **Save Changes**.

### Mandatory verification

Test in the Bedrock console guardrail playground before you submit.

| Input | Required result |
|---|---|
| The `c1` chest-pain sample question | **INTERVENED** |
| The `c3` FHIR ExplanationOfBenefit sample | **NOT** intervened |
| The `c4` TCGA patient-count question | **NOT** intervened |
| The `c2` course sections paragraph | **NOT** intervened |
| A `c5` question such as "How many legs does a cow have?" | **NOT** intervened |

The verification script also contains the **four exact live prompts that failed on
August 25**: Type-2 diabetes/metformin dosage, lower-right abdominal
pain/appendicitis, hydrocodone dosage for a 180-pound caller, and the Johnson
Silver-HMO member-ID/SSN/DOB request. All four must return `INTERVENED` before a
scored run. Then confirm the supervisor shows the same guardrail ID/version and
save the supervisor again.

The `10:33:43` live run answered the diabetes/metformin case with exactly
`I cannot help with that request.` and earned the `c1` +400 with no life loss.
That is operational proof that the attached configuration scores this case, so
Pathfinder now defaults `c1` confidence to 0.97. The combat JSON does not expose
an ApplyGuardrail trace, so it cannot distinguish Bedrock intervention from an
exact fallback match; keep `guardrail/verify.py` as the attachment/version check.
If the guardrail is detached or unverified, override `c1` to 0.0 immediately.

If `c1` does not fire, widen the topic definitions first. Only if that fails should you raise Misconduct to LOW — and then re-run the whole table, because that is exactly what starts blocking claims questions.

---

## 8. Lambda tool reference

Source lives in `tools/`. Each file exposes `lambda_handler` and is dependency-free (standard library only).

Create the tool in the UI with the **short name** below, then paste the file contents into its `lambda_function.py`. Short tool and parameter names cost fewer output tokens on every call.

> ### The `lambda_handler` docstring IS the tool schema
>
> The UI generates each tool's name and parameter list from a structured block in
> the `lambda_handler` docstring:
>
> ```text
> ---
> Tool: route
> Description: ...
> Parameters:
>     game_map     (required) - the 2D grid array as JSON string or list
>     start_pos    (optional) - starting position as [row, col]
>     strategy     (required) - routing mode
> ---
> ```
>
> Proof it is parsed: after the doorkey docstring was edited, the invoked tool name
> in the combat log changed from `doorkey___analyze_map` to
> `doorkey___analyze_map_and_build_pass_string`.
>
> **A tool saved without this block can regenerate with no parameters.** The model
> then cannot pass `game_map`, the navigation turn never returns, and the run ends
> with `tokensUsed: 0` and zero movement — exactly what the `12:09:28` log shows.
> Never remove the block, and after every save re-open the tool and confirm
> `game_map`, `start_pos`, and `strategy` are still listed.

| UI tool name | Source file | Used for | Attach to |
|---|---|---|---|
| `pathfinder` | `tools/pathfinder.py` | routing, coins, spikes, lives, treasure | Pathfinding_Specialist |
| `courseoptimizer` | `tools/course_optimizer.py` | `c2` | Supervisor |
| `claims` | `tools/claims_eob_solver.py` | `c3` | Supervisor |
| `permit` | `tools/permit_calculator.py` | `c18` | Supervisor |
| `opendata` | `tools/registry_lookup.py` | `c4` | Supervisor |
| `doorkey` | `tools/map_analyzer.py` | `c30`, `c40` | Supervisor |
| `pycalc` | `tools/code_executor.py` | arithmetic fallback | Supervisor |

> The August 25 live logs show the deployed UI name is `courseoptimizer`.
> The 13:25 run called it for both `c2` prompts: the first minified JSON passed,
> while Haiku summarized the second correct analysis as prose and failed grading.
> Keep this deployed name and enforce verbatim `answer` relay in the Supervisor
> prompt. `game-data.md` uses the logical name `course_optimizer`.

### `pathfinder`

Input:
```json
{
  "game_map": [["start","normal"],["wall","treasure"]],
  "start_pos": [0, 0],
  "strategy": "use strategy max_points",
  "lives": 5,
  "tile_overrides": {},
  "challenge_confidence": {},
  "count_tile": "c5",
  "max_challenges": 12,
  "time_limit": 300
}
```

Output — deliberately minimal and flat by default:
```json
{"path":["down","right","right"],"start_position":[0,0],"c5":"SIX"}
```

Pass `"verbose": true` only for direct smoke tests. It adds coordinates,
strategy, score estimates, probabilities, visited tiles, and notes; the same
information is always written to CloudWatch without inflating the agent answer.

Design notes:

- **Direction words by default.** The stock pathfinding tool shipped with the real game returns direction words, and its sub-agent prompt returns "only the path array" — so directions are the proven contract. Switch with `{"output_format":"coords"}` only if a practice run proves absolute coordinates are needed.
- **Minimal response is mandatory.** The supervisor echoed a verbose tool response in a live run, used 1406+ output tokens, and lost the token bonus. Diagnostics are not free if the model repeats them.
- **Flat structure.** The port's `path_parser._try_json_path_object` uses `\{[^{}]*"path"\s*:\s*\[.*?\][^{}]*\}`, which rejects nested braces, so no nested `stats` object.
- **Treasure is blocked** until the final segment.
- **Repair maximises expected score, not a damage budget.** A target is dropped only when dropping it *raises* the expected final score. The old loop tried to force `certain + expected x 2.5` below `lives - 1`; on the live 9x9 board that budget was unreachable (two forced spikes plus the door came to 4.25 of 5 lives), so it sacrificed whatever was droppable to chase it — and once challenges were protected, the only candidates left were the two 2500 point coins. `simulate_path` already prices risk exactly, because death forfeits the life and treasure bonuses while banked points survive, so a second damage heuristic could only ever contradict it.
- **Door-gated loot is retried after unlocking.** Targets are assembled in repeated passes, so a tile sealed behind a locked door is deferred rather than discarded. The `12:19:00` board hid a 2500 point coin in a walled alcove whose only entrance was the red door; single-pass assembly dropped that coin, which also stripped the key/door package of the reward justifying it, so key, door, and coin were all skipped together. A door with no key on the map is still refused and its loot correctly reported unreachable.
- **Challenges sorted by confidence descending**, so the riskiest attempt is last and your banked points are already safe.
- **Key-door packages are atomic through repair.** The planner preserves exact key/door coordinates, routes key before door, branches terminal door failure correctly, counts each spike once, and includes the package only when its expected final score beats going directly to treasure. Generic risk repair evaluates ordinary targets by marginal score first and cannot silently delete an accepted positive-EV package; if ordinary repair changes package economics, the package is re-evaluated as one unit.
- **Red door defaults to 0.90 for `max_points`.** This is the explicit leaderboard-risk policy and clears the one-spike 0.84 break-even; other door colors remain 0.0. Use `no_door` immediately if the controlled key/memory smoke fails.
- **Spikes are not globally forbidden.** A one-life spike is a 250-point cost and is intentionally taken when it unlocks a sufficiently reliable +50 key/+1000 door package.
- **Challenge budget** includes key and door invocations and derives from the map time limit.
- **Greedy nearest-neighbour ordering.** Movement has no direct score cost, but shorter routes reduce timeout and relay risk.

Always inspect CloudWatch `strategy_used`, `reaches_treasure`, and package notes before trusting a deployment.

### `claims` — `c3`

Accepts compact rows *or* raw FHIR:
```json
{"rows": [["99213", 150.00, 120.00, 25.00, 0.00, 71.00, 0, ""]]}
```
Row layout: `[code, submitted, eligible, copay, deductible, benefit, denied, carc]`

Returns `{"answer": "{\"TotalAllowed\":120.00,...}"}`.

Two implementation details that matter:

- The non-denied member formula `copay + deductible + (eligible − benefit − copay − deductible)` collapses algebraically to **`eligible − benefit`**.
- Money is formatted as text, because `json.dumps` renders `1250.00` as `1250.0`. Grading via `json_exact_match` compares parsed numbers so it would tolerate either, but `exact_match` would not.

**Prefer compact rows.** Raw EOB JSON costs roughly 1000 output tokens per challenge; rows cost about 80. That difference lands directly in your token bonus.

### `course_optimizer` — `c2`

```json
{"sections": [{"sectionId":"SEC-101","courseName":"BIO-201","instructor":"Dr. Smith","timeSlot":"MWF 9:00AM","enrolled":12,"capacity":35}]}
```

Flags below 50% (exactly 50% is not flagged), consolidates same course + both flagged + different time slots + fits kept capacity, keeps the higher enrollment, and preserves input order.

**Rule discovery:** the "combined enrollment fits the kept section's capacity" condition is *unreachable*. If both sections are flagged, each enrollment is under half its capacity, and the kept section is the larger of the two — so the combined total always fits. The guard remains as defensive code with a test documenting it, so a change to the flag threshold trips immediately.

### `permit` — `c18`

```json
{"text": "Calculate the energy impact of a 100 MW solar farm in California."}
```
or `{"capacity_mw": 100, "kind": "solar"}`.

Capacity factors solar 0.25, onshore wind 0.35, offshore wind 0.45. Detection checks *offshore* and *onshore* before the generic *wind*.

Rounding of the two integer fields is ambiguous in the rules — floor and round both reproduce the worked example. Default is `floor`; pass `{"rounding":"round"}` if a submission grades wrong.

### `opendata` — `c4`

```json
{"name": "The Cancer Genome Atlas"}
```

Tries the dataset YAML in `awslabs/open-data-registry` first (identical wording, far smaller payload), then falls back to the rendered `registry.opendata.aws/<slug>/` page. Results are cached in `/tmp`, which persists across invocations in a warm container.

Network access is restricted to those two hosts, with resolved addresses checked against private ranges. An open URL fetcher inside a Lambda is an SSRF hole that can reach link-local metadata.

Slug resolution generates both acronym forms — `tcga` keeps the leading article, `cga` drops it.

Grading is wording-sensitive, so the agent must copy the phrase from the source, including commas and units: `11,000`, not `11000` and not a sentence.

### `doorkey` — `c30` / `c40`

```json
{"game_map": [["c5","c5"],["c5","treasure"]], "count_tile": "c5", "key_text": "Your key character is: X"}
```

Returns only `{"answer":"X-THREE"}`. Pass `{"verbose":true}` for diagnostics.

**Send `game_map` plus `key_char` and never send a count.** The model cannot be
trusted with this number: two live runs lost the door because the supervisor sent
the *path length* (`EIGHTY`, then `NINETY-SIX`) instead of the `c5` census, which
was `FOUR`. The tool now enforces this:

- `game_map` always wins, even when a count is also supplied.
- A supplied `count_word` must be a real number word.
- A supplied count above 40 is rejected as an obvious path length, with an error
  telling the caller to resend `game_map`.

Roughly 600 tokens to resend the grid costs about 30 points of token bonus and
protects 1000 (door) + 3000 (treasure) + up to 1250 (lives).

Cheaper path: `pathfinder` already returns the same count as a flat field on an invocation you have already paid for. Read that from memory instead of spending a second invocation — invocations also consume wall-clock time against the map limit.

### `pycalc` — fallback

AST-allowlisted sandbox. Imports, attribute access, dunder names, `class`, `with`, `try` and `raise` are all rejected, and real builtins are never exposed.

Deploy it with **no AWS permissions beyond CloudWatch logging, no VPC access, a short timeout, and no sensitive environment variables**. The allowlist is the primary defence, not the only one.

---

## 9. Deployment

### Verify the exact live tool chain BEFORE changing planner logic

`tools/pathfinder.py` writes `v=2026-08-25.7-json-array-relay` and full route
diagnostics to CloudWatch. The minimal scored response deliberately omits the
version so Haiku has one simple job: extract `path` as a valid JSON array. Direct
verification uses `verbose=true`, which returns the version. Invoke the **exact
function or alias attached to the live Pathfinding Specialist**:

```powershell
python tools/verify_deployed.py <exact-lambda-function-or-alias>
```

On the reconstructed 12:47 board, this source returns 97 moves, all 16 ordinary
challenges, both coins, then `c40 → c30 →` the gated coin `→ treasure`. The old
source returns 95 moves with the same ordinary challenge and key/door coverage
but misses the gated coin. The live combat log returned 77 moves and omitted the
left challenges, key, door, and gated coin. Therefore neither local planner
produced that live path.

The combat JSON does **not** contain the Lambda operation/function, raw payload,
raw response, version, or Pathfinder Specialist trace. Consequently, the
byte-identical 12:19 and 12:47 paths do not by themselves prove stale source.
Use the scored-run timestamp and CloudWatch to separate the cases:

- No invocation at that timestamp: the specialist did not call this Lambda, or
  is attached to a different function/alias/tool operation.
- `v=2026-08-25.7-json-array-relay`, `steps=97`: Lambda is correct. The
  game-facing answer must still be a quoted JSON array, never CSV.
- Current `v` but 77 steps: compare `strategy_used`, start, dimensions, and tile
  counts in `DIAGNOSTICS`; the live request differs from the reconstructed input.
- Missing/wrong `v` on a verbose direct invocation: the invoked target is not
  this exact code contract.

Do not change route heuristics until one of those observations identifies the
failing layer.

1. Treat `tools/pathfinder.py` as the canonical source. The active seed copy at
   `agent-config/tools/Pathfinder/index.py` is the old BFS implementation and
   must **not** be deployed for a scored run until it is synchronized.
2. Open the pathfinding tool's pencil icon and confirm the exact Lambda/tool you
   are editing before pasting the canonical file.
3. For each tool above, save and inspect the regenerated MCP schema and parameter names.
4. Attach tools to agents per section 3; keep `pathfinder` only on the specialist.
5. Create `DungeonMemory` and `VioletVaultGuard`, attach both to the supervisor,
   save, and record the attached guardrail ID/version.
6. Direct-invoke Pathfinder once with `verbose=true`. A 200 response with
   `path`, `start_position`, `c5`, and the expected `v` confirms the callable
   contract. The default scored response intentionally omits `v`.
7. Run the three-strategy deployment fingerprint from section 5 and verify the
   requested strategy in CloudWatch. Identical routes are a deployment blocker.
8. Paste the system prompts, then run **Play the Board**.

Run the test suite locally first:

```powershell
python -m pytest tools/tests -q
```

82 tests currently pass, including the live 9x9 board (both coins, all 16
challenges, key and door), door-gated loot recovery, hallucinated-count
rejection, key/door orphan invariance, package economics, terminal door branching,
key-before-door order, positive-EV package preservation during risk repair,
exact-coordinate package removal, unselected-key avoidance, one-time spike
consumption, locked-door safety, minimal Lambda output, verified-guardrail `c1`
selection plus its explicit rollback, parser round trips, and all deterministic
challenge tools.

---

## 10. Answer contracts per challenge

The token bonus is decided here. Aim for the shortest correct string.

| Tile | Exact output shape |
|---|---|
| `c1` | Nothing to do — the guardrail must intervene on the question |
| `c2` | `{"FlaggedSections":[...],"Consolidations":[...],"NoAction":[...]}` minified |
| `c3` | `{"TotalAllowed":120.00,"MemberResponsibility":49.00,"DeniedLines":[]}` minified |
| `c4` | The bare fact, matching page wording: `11,000` |
| `c5` | The bare answer: `4` |
| `c18` | `{"annual_generation_gwh":219.0,"carbon_offset_tons":91980,"homes_powered":20857}` |
| `c30` | `X-SIX` |
| `c40` | `Thanks` |

**On numeric hedging:** answering `4 (four)` is safe under `contains_match` but **fails** `exact_match`, which is strict equality after trim and lowercase. You cannot see which strategy a tile uses at runtime, so do not hedge — answer the bare form and learn the tile's behaviour from practice runs.

---

## 11. Risk policy

### The door is a package bet, and spike cost changes the threshold

Evaluate `matching key → matching door → treasure` as one dependency chain. Do
not reject the key because its isolated +50 reward is smaller than a spike.
Assuming the key response is reliable and the package requires `k` new one-time
spikes:

```
skip package → 5×250 life bonus + 3000 treasure = 4250
package fail → key's 50 remains banked; door kills the run
package win  → 50 key + 1000 door + (5-k)×250 lives + 3000 treasure
break-even door confidence = 4200 / (5250 - 250k)
```

| New spikes (`k`) | Break-even door confidence |
|---:|---:|
| 0 | 0.8000 |
| 1 | 0.8400 |
| 2 | 0.8842 |
| 3 | 0.9333 |
| 4 | 0.9882 |

Therefore the user's red route is correct when one spike obtains the red key and
the red-door pipeline is above about **84%**: the successful package gains
`50 + 1000 - 250 = 800` net versus preserving that life. `pathfinder.py` does
not use this table as a shortcut; it explicitly simulates the complete baseline
and candidate routes, so consumed spikes are not charged twice, key color/order
is enforced, prior loot is banked, and terminal door failure receives no life or
treasure bonus.

The `10:33:43` run scored 8,551 but never reached `c40` or `c30`, so it
provides no direct memory or door evidence. The omission was traced to a second,
generic repair pass: package selection accepted `c40 -> c30`, then reverse-order
risk dropping selected `c30` as the last damage tile and removed its key. The
canonical planner now preserves exact-coordinate packages, drops the lowest-loss
ordinary risk first, protects a still-positive package from generic reserve
math, and rechecks package EV if repair changes its route. The leaderboard policy
keeps red `c30` at **0.90**, above the one-spike break-even, because `doorkey`
builds the answer deterministically. Validate this in one controlled run; on any
key recall or door failure, switch to `no_door` or override `c30` to 0.0 before
the next scored run.
Keep the package last, with treasure immediately after the door.

### Challenge break-even confidence

Basic thresholds, ignoring the treasure at risk:

| Tile | Reward | Damage | Break-even |
|---|---:|---:|---:|
| `c3`, `c4`, `c2` | 750 | −1 | 25.0% |
| `c18` | 600 | −1 | 29.4% |
| `c1` | 400 | −1 | 38.5% |
| `c5` | 250 | −1 | 50.0% |

These are floors, not targets. Once failure could cost the run, the real threshold rises steeply. The planner therefore uses complete expected-score outcome branches, then repairs lower marginal-value ordinary targets first while preserving an accepted positive-EV key-door package.

### Default confidence values

Conservative on purpose. Raise per tile only when the combat log proves the specialist works.

```
c2 0.97   c3 0.97   c18 0.97   c5 0.99   c40 0.95
c1 0.97   c4 0.97   c6 0.50    c17 0.50  c30 0.90
```

`c1` is 0.97 because the configured guardrail produced the exact blocked message
and won the live diabetes/metformin challenge for +400 without damage. Keep the
full positive/negative verification suite as the deployment gate. If the
verified guardrail version is not attached, pass `challenge_confidence={"c1":0.0}`
to exclude Violet Vault immediately.

---

## 12. Token policy

```
token bonus = 1000 − (total output tokens ÷ challenges visited)
```

- Only **output** tokens count, and tool-call arguments are output tokens.
- The pathfinding call's tokens go into the same pool.
- Because it is an **average**, adding a terse challenge *raises* the bonus, while one verbose answer is diluted across all challenges. Trim your worst offender first.
- The navigation prompt is input, so it is free. Make it as explicit as you like.
- Never let the agent emit the map twice. That is why the map only travels supervisor → Pathfinding_Specialist → tool, and why no other sub-agent receives it.

---

## 13. Test and submit playbook

Verification order — do not move on with a failing stage:

1. `python -m pytest tools/tests -q` — all 78 green.
2. Guardrail playground plus all four live `c1` cases — required interventions and non-interventions correct.
3. Direct-invoke deployed Pathfinder with `safe`, `get_coins`, and `max_points`; verify route differences and CloudWatch strategy fingerprints.
4. Play with `safe` — confirms path execution and treasure.
5. Play with `get_coins` — confirms both coins, start coordinate, terse relay, and treasure without optional medical risk.
6. Capture the **exact navigation grid** from the live prompt. Do not substitute `combat-logs/replay.py`; its board is an obsolete partial reconstruction.
7. After memory is warm, run a controlled red package only when direct tool output shows key-before-door, an economically justified spike count, and treasure last.
8. Play with `max_points`; read every event against the theoretical maximum.
9. Fix exactly one thing at a time. Changing prompts, strategy and three tools in one run tells you nothing.
10. Once every tile wins, trim tokens, then submit repeatedly. Best Score is the maximum over submissions; evaluation can take up to 15 minutes.
11. Do not touch a working configuration after the last good run.

Per-run record to keep:

```json
{"strategy":"max_points","score":0,"treasureReached":true,"livesRemaining":0,
 "tokenBonus":0,"totalTokens":0,"coinsCollected":0,"challengeResults":[],"route":[]}
```

Track *why* points were lost, not just the total: wrong answer, verbose answer, missed coin, spike hit, early treasure, failed door, or invalid path.

---

## 14. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Lives drop to 0 on the first move | Path contained a wall or off-grid step | Check `reaches_treasure`; the planner refuses illegal steps, so suspect the relay prompt |
| Run ends early with points but no treasure | Path crossed treasure mid-route | Treasure must be last; confirm the tool output was relayed unmodified |
| Every challenge returns the blocked message | Guardrail content filters too aggressive | Set all filters to NONE, rely on denied topics |
| `c1` scores zero despite a polite refusal | Guardrail never intervened | Grading calls ApplyGuardrail on the question — widen the topic definitions |
| `c3` or `c4` suddenly failing | Guardrail firing on clinical vocabulary | Re-run the section 7 verification table |
| `c30` answered wrongly | Key-delivery and door-question actions were inverted, or a tool trace polluted exact-match grading | At `Red Key N is: X`, call doorkey with `phase="prepare"`, retain `door_answer`, and answer `Thanks`; at `What is red key N?`, call no tool and emit retained `X-WORD` exactly |
| `c2` analysis is correct but loses | Haiku summarized `courseoptimizer.answer` as prose | Output the answer field verbatim; require minified JSON with `FlaggedSections`, `Consolidations`, and `NoAction` |
| Token bonus near zero | Agent explaining itself | Enforce verbatim relay; check for Markdown and preamble |
| Path arrives truncated | Map re-emitted through too many hops | Keep the map on one hop only |
| Run does not finish in time | Too many challenge invocations | Lower `max_challenges` or raise `seconds_per_challenge` |
| `get_coins` and `max_points` return the same long route | Stale Lambda, exact-string parser, or specialist omitted `strategy` | Direct-invoke both, inspect CloudWatch `strategy_used`, and redeploy canonical `tools/pathfinder.py` |
| Direct tool works verbose but default Lambda returns 400 | Old handler reads diagnostic keys absent from minimal output | Deploy the handler with safe minimal-result logging and repeat the direct invocation |
| Planner avoids a profitable red key behind one spike | Stale Lambda or generic risk repair overriding package selection | Deploy canonical planner; verify `door c30 package included`, an ordinary target is dropped first if needed, and no contradictory package-removal note appears |

---

## 15. Confirmed rules versus port inferences

The live game is **not** this repository. This repo is a community port, so treat
its internals as strong hints, not law. Everything below is separated by source
so you know what to re-verify on the real board.

### Confirmed by `game-data.md` / `workshop-reference.md` — build on these

- All point and damage values: treasure 3000, coins 2500, `c2`/`c3`/`c4` 750, `c18` 600, `c1` 400, `c5` 250, `c40` 50, `c30` 1000/−5, `c8` −1.
- 5 starting lives, 250 per life remaining.
- Token bonus `1000 − (total tokens ÷ challenges visited)`.
- A run ends on treasure, on zero lives, or when time runs out.
- `course_optimizer` is the challenge's documented logical tool name; the current live UI registration proven in the August 25 logs is `courseoptimizer`.
- The exact answer formats for `c2`, `c3`, `c18`, `c4`, `c5`, `c30`, `c40`.
- The `c30` pass string is `key character` + `-` + tile count in capitals.
- The `c40` interaction requires saying `Thanks`.
- Only the pathfinding sub-agent's runtime prompt is under your control; challenge prompts are generated by the game.
- Lambda tools may not call external models. No hardcoded kiosk answers.
- The stock tool returns **direction words** and its prompt returns only the path array.
- The navigation prompt reaches the tool as `strategy`, selected with text like `use strategy get_coins`.

### Inferred from this port — verify on the real board before relying on them

| Inference | Where it came from | If it turns out different |
|---|---|---|
| Wall or off-grid step is instantly fatal | `game_runner.py` | Behaviour is only ever safer; the planner never emits one regardless |
| Consumed tiles are inert on revisit, spikes included | `game_runner.py` | Router would double-count spikes; raise the spike penalty |
| Earned points survive death | `game_runner.py` | Risk-last ordering becomes even more important, not less |
| Only output tokens count | `game_runner.py` v2 | Terse answers remain correct either way |
| Pathfinding call tokens join the same pool | `game_runner.py` | Keep the map on one hop regardless |
| Grading strategies: exact / contains / json / guardrail | `challenge_grader.py` | Do not hedge answer formats; answer the documented shape |
| `guardrail_block` calls ApplyGuardrail on the question | `challenge_grader.py` | A firing guardrail is required either way |
| Nested braces break the primary path regex | `path_parser.py` | Flat output is harmless in any engine |
| Coordinate pairs are accepted as a path | `path_parser.py` | **This one matters** — default is directions for that reason |
| Door damage is applied on a wrong answer too | `game_runner.py` | Package EV becomes less conservative if live behavior differs |
| Map `timeLimit` defaults to 300s | `maps-api/index.ts` | Tune `max_challenges` from the observed board |
| Door failure is terminal and receives no later treasure/life bonus | Port runner plus five-damage rule | Verify in a controlled practice run before trusting observed confidence |
| `combat-logs/replay.py` grid represents the current live map | **It does not**; it conflicts with traversed live coordinates and omits tiles | Capture the exact navigation-prompt grid before any route replay |

**Practical rule:** where `game-data.md` and the port disagree, follow `game-data.md`
and pass the real values through `tile_overrides` and `defaults` rather than editing
the tool's constants.

---

## 16. Compliance

`workshop-reference.md` prohibits calling external models from Lambda tools, and `game-data.md` prohibits hardcoded kiosk answers and out-of-scope agent use.

Everything here stays inside those lines. All tools are deterministic standard-library Python. The `c30` pass string is **derived at runtime** from the live map census and the key character the game hands over — it is dynamic logic, not a stored answer. No tool contacts a model, and `opendata` reaches only the two AWS Registry hosts the challenge names.
