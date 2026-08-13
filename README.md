# Solidity Auditor

A security agent with a simple mission - findings in minutes, not weeks.

Built for:

- **Solidity devs** who want a security check before every commit
- **Security researchers** looking for fast wins before a manual review
- **Just about anyone** who wants an extra pair of eyes.

Not a substitute for a formal audit - but the check you should never skip.

## Demo

_Portrayed below: finding multiple high-confidence vulnerabilities in a codebase_

![Running solidity-auditor in terminal](../static/skill_pag.gif)

## Usage

```
Install https://github.com/pashov/skills/ and run solidity auditor on the codebase
```

```
run solidity auditor on *specified files*
```

```
update skill to latest version
```

## Tips

- **Target hot contracts.** Rather than scanning an entire repo, point the tool at the 2-5 contracts you're actively changing. Smaller scope means denser context for each agent and higher-signal findings.
- **Run more than once.** LLM output is non-deterministic — each run can surface different vulnerabilities. Two or three passes over the same code often catch things a single pass misses.

## Local fork: three additional agents

This copy is upstream **v3** with three agents added that are not in the upstream
skill. Base files are unmodified except `SKILL.md`, which routes to them.

| Agent | Bundle | Prompt | Covers |
|-------|--------|--------|--------|
| `signature-trust-agent` | 13 | single-specialty (3a-i) | What a signature actually proves vs. what the contract assumes: digest binding, replay, chainId, nonce and deadline handling |
| `hook-ordering-agent` | 14 | single-specialty (3a-i) | Hooks / callbacks / extension points as untrusted execution contexts that observe intermediate state, return malicious deltas, or re-enter |
| `signature-gap-agent` | 15 | gap-hunter (3a-ii) | The seam between signature trust x access control x execution trace |

Agent count is therefore **15**, not 12. Every count in `SKILL.md` has been
updated to match.

### Compatibility notes

- All three use "Add to FINDINGs", so they extend the `shared-rules.md` output
  contract rather than replacing it. v3's `group_key` field (used by dedup) is
  inherited correctly.
- v3's expanded `asymmetry-agent` has no hook or callback coverage, so
  `hook-ordering-agent` remains a genuine gap-filler rather than a duplicate.

### Rebasing again

These three files are the entire local delta:

```
references/hacking-agents/signature-trust-agent.md
references/hacking-agents/signature-gap-agent.md
references/hacking-agents/hook-ordering-agent.md
```

To take a future upstream release: start from upstream, copy those three in, then
re-apply the `SKILL.md` edits (bundle table rows 13-15, agent counts, and the
prompt-routing note). Do NOT merge in the other direction — a fork pinned to an
older base silently accumulates stale guidance while upstream improves.
