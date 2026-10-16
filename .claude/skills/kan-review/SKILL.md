---
name: kan-review
description: Format and post the review of a finished feature on its Kan card and move the card to the Review list. Use after the reviewer agent has reviewed a change, when reporting review results on the Kan board.
---

# Kan feature review

Tasks live on the Kan board described in CLAUDE.md ("Task tracking (Kan)"). Use `scripts/kan.py`; never raw API calls and never print the API key. The script needs `KAN_API_KEY` and `KAN_BOARD_ID` from `.env.kan` (see `.env.kan.example`); if missing, tell the user to set them.

## When

After the `reviewer` agent has produced its findings for the change (and the tester has reported). Not before: moving a card to `Review` means an independent review has actually happened.

## Procedure

1. Find the card: `scripts/kan.py board` (card id is the `publicId`, shown next to the title). The change's `proposal.md` usually names the card.
2. Build the comment from the template below using real results only: the reviewer's actual findings and the tester's actual command output. Never claim a check passed unless it was executed; list anything that could not run.
3. Post it: `scripts/kan.py comment CARD_ID "<text>"`.
4. Move the card: `scripts/kan.py move CARD_ID Review`.
5. Confirm with `scripts/kan.py show CARD_ID` or `board` that the card is in `Review`.

If valid high or medium findings are still unaddressed, still post the review but state clearly that fixes are pending; keep the card in `Review` only if the user agrees, otherwise leave it in `In-progress`.

## Comment template

Keep it short, plain text (Kan comments are not guaranteed to render Markdown), no secrets, no prompt/response content:

```
Review: <change-name>
Branch: <branch>   Commits: <n or "not committed">   PR: <url or "none">

Verdict: <no blocking findings | fixes pending | blocked>

Findings:
- High: <count> - <one line each, file:line>
- Medium: <count> - <one line each>
- Low: <count> - <short list>
(or "No findings")

Checks: pytest <passed/failed/skipped>, ruff <ok/fail>, black <ok/fail>, mypy <ok/fail>
Not run / limitations: <e.g. integration tests need PostgreSQL>
Spec: <deviations from the OpenSpec spec, or "none">
Open items: <decisions needed from the user, follow-ups>
```

## Rules

- One review comment per review round; for a re-review after fixes, post a new comment that states what changed.
- Do not move a card to `Done`; only the user does that after the PR is merged.
- Summaries only; link or name files rather than pasting code.
