---
name: prompt-engineering
description: Scaffold and refine system prompts for AI agents — structure, examples, output schemas, prompt caching, and iteration discipline. Use when starting a new agent, upgrading models, or chasing a regression in quality.
---

# prompt-engineering

Provides an opinionated structure for system prompts and an iteration discipline that prevents the usual death spiral of "tweak prompt → eyeball one example → tweak again." Pairs with `evals` (without numbers, every change feels like an improvement) and `logging` (cache hit rates, token counts, latency are the prompt's vital signs).

## When to trigger

- User types `/prompt-engineering`.
- User is writing a new system prompt or rewriting one that grew organically.
- User says "the agent keeps doing X" / "it ignores my instructions" / "it makes up Y."
- Model upgrade (4.6 → 4.7, etc.) — old prompt assumptions may not hold.

## System prompt skeleton

Order matters. Models attend most reliably to the start and end. The skeleton (top to bottom):

1. **Role** — one sentence. Who the agent is and what it does. Skip the puffery ("brilliant world-class expert"). The model is not motivated by flattery; specificity is what helps.
2. **Operating context** — what environment it runs in, what it has access to, what it does not. Tools available, time/timezone if relevant, user persona if known.
3. **Task instructions** — the core of the prompt. Positive form, imperative, one rule per line.
4. **Output format** — exact shape required. Show, don't describe.
5. **Examples** — 1–3 worked examples. Diverse (don't show three near-identical cases). Include at least one negative/edge case.
6. **Final reminders** — the 2–3 rules most often violated. Repeating a critical rule at the end measurably improves adherence.

See `templates/system_prompt.template.md` for an annotated version.

## Rules that earn their place in a prompt

A prompt is a budget — every line you add costs attention and tokens. Cut anything that doesn't change behavior.

- **Positive over negative.** "Reply in JSON" beats "Don't reply in prose." Models complete patterns, and negative phrasing still primes the wrong pattern.
- **Show, don't describe.** One example of the desired output beats three paragraphs about it.
- **Imperative, not conversational.** "Return only the JSON object" beats "It would be great if you could just return the JSON."
- **One rule per line.** Compound sentences hide constraints. Bulleted lists are read more reliably than prose.
- **Concrete over abstract.** "Use 4-space indentation" beats "Use consistent indentation."
- **Constraints near the data.** Put output-format rules just before the user input, or repeat them there — long-context attention drops in the middle.

## Output schemas

Pick the strongest constraint that fits:

| Need | Tool |
| --- | --- |
| Strict schema, validated server-side | Tool use with `input_schema` (Anthropic) / structured outputs (others). Treat the tool as the schema enforcer. |
| Loose JSON | Ask for JSON, parse with `json.loads`, retry once on failure. Wrap in `<output>…</output>` tags to ease extraction. |
| Free-form prose | Constrain *shape* (length, sections) but leave wording free. |

Never ask for "JSON or prose, your choice." The model will pick differently each call and your parser will break.

## Examples (few-shot)

- **Use few-shot when** the task is unusual, the output format is fiddly, or the model defaults to a wrong-but-plausible alternative.
- **Skip few-shot when** task-following is already reliable — examples cost tokens, slow the prompt, and risk *style mimicry* (the model copies superficial features of your examples).
- **Diversity matters more than quantity.** Three diverse examples > seven near-duplicates. Include the awkward cases.
- **Order matters.** The last example is the most influential — put the strongest representative there.
- **Don't leak the eval set into examples.** Few-shot examples that overlap with eval cases inflate scores without improving quality.

## Anthropic prompt caching

Cache structure (top-down, most stable first):

1. **System prompt** — `cache_control: ephemeral`.
2. **Tools** — schemas rarely change; cache them.
3. **Static context** — long reference docs, codebase context, retrieved chunks that are stable across the conversation.
4. **Conversation history** — cache up to the last stable turn.
5. **User input** — never cached; this is the variable suffix.

Rules:

- Cache breakpoints must be **prefix-aligned**. Anything you cache must be byte-identical across calls up to that point — re-ordering or templating with timestamps blows the cache.
- Cache TTL is **5 minutes** (default) or **1 hour** (extended). Pick based on actual call cadence — don't pay for 1-hour TTL if your agent fires every 30 seconds.
- Measure with `cache_read_input_tokens` and `cache_creation_input_tokens` in the response. Hit rate should sit above 80% on a hot path; if it doesn't, something is reshuffling the prefix.
- See `templates/prompt_cache_layout.py` for the SDK call shape.

## Reasoning and extended thinking

- For multi-step tasks (planning, math, code review), enable extended thinking. The model uses thinking tokens to work out the answer before committing — accuracy goes up, latency too.
- Don't ask the model to "think step by step" in the user-facing reply when extended thinking is on — the thinking happens in a separate channel.
- For simple lookups or short responses, extended thinking is a tax with no return. Default off; turn on per-task.

## Iteration discipline

The trap: tweak prompt → look at one output → tweak again. After ten iterations you have ten changes and zero understanding of which one helped.

Discipline:

1. **One change at a time.** Diff one variable per iteration.
2. **Always re-run evals**, not just one prompt. See `evals` skill.
3. **Version every prompt.** Hash the rendered prompt; log the hash with each call so you can attribute regressions.
4. **Bisect regressions.** When a metric drops, you should be able to identify the exact prompt change that did it by replaying logged hashes.
5. **Keep a prompt CHANGELOG.** One line per change with the eval delta. After a month it tells you which kinds of changes actually move the number.

## Common failure modes

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| Model ignores a rule | Rule is buried in the middle, or phrased negatively | Move to top/bottom; rephrase positively |
| Format drift | No example of correct format, or examples are inconsistent | Add 1–2 examples with the *exact* format |
| Hallucinated facts | Source material missing or unclearly attributed | Move source into a `<source>` block, instruct to cite |
| Refuses benign requests | Over-aggressive safety language in system | Remove generic safety boilerplate; trust model's defaults |
| Conflicting outputs across calls | Conflicting instructions in prompt | Find the contradictions — usually two rules added at different times |
| Cache miss every call | Something in the prefix changes (timestamp, user ID interpolated too early) | Move variable content after the cache breakpoint |

## What this skill will NOT do

- Add flattery to the role line ("You are an exceptional, brilliant…"). It does not help and inflates tokens.
- Encourage chain-of-thought instructions in the user message when extended thinking is available — those are now separate mechanisms.
- Write prompts that defend against jailbreaks by piling on warnings. Defense-in-depth lives in `security-guardrails` and tool-level checks, not in scolding the model.
- Iterate on prompts without an eval set. If `evals` isn't set up, run that skill first.

## Templates

- `templates/system_prompt.template.md` — annotated skeleton with the six sections and a worked example.
- `templates/prompt_cache_layout.py` — Anthropic SDK call with correct cache_control placement and a hit-rate logger.
