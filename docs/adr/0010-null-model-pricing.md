# 10. Model pricing stays null until verified

**Status:** Accepted

## Context

The AI layer reports what each analysis cost. That needs per-token rates,
and the first version of `models.json` shipped with rates written from
memory.

When a key became available, the catalog turned out to be wrong in a more
basic way: `gemini-2.0-flash`, shipped as the *fallback*, does not exist.
The entire 2.5 line returns 404 on `generateContent` despite appearing in
`models.list()`. If the model ids were wrong, the rates had no better claim
to being right.

## Decision

All `input_usd_per_mtok` and `output_usd_per_mtok` are `null`.
`pricing_verified_on` is `null`. Cost is reported only once someone fills in
a rate they have confirmed against the published price list.

## Rationale

Token counts come back exact from the API and are always reported. Cost is
tokens times a rate, and the rate is the only part this project cannot
verify.

A wrong cost figure is worse than an absent one. It looks authoritative, it
propagates into a README and onto a CV, and nothing about it signals that it
was a guess.

## Consequences

`estimate_cost_usd` returns `None` rather than `0.0` when rates are absent —
zero would read as "this was free". `/metrics` omits the cost field entirely
rather than reporting zero. The word "estimated" appears everywhere cost
surfaces, even once rates are filled in.

A test asserts a rate pair is either complete or absent, since a half-filled
pair would silently under-report.

`scripts/check_models.py` verifies model ids by actually calling each one
with a real schema — listing is not enough — and reports which entries still
have no rates.
