# 4. Own LLM abstraction rather than LiteLLM

**Status:** Accepted

## Context

LiteLLM is the standard answer to provider abstraction: one interface over a
hundred model APIs, with routing, fallbacks, cost tracking and caching. It
was cloned and read as part of the research for this project.

## Decision

Take the patterns, not the dependency. `marketpulse/ai/` implements a model
registry, a fallback chain, error classification and token accounting in
roughly 400 lines.

## Rationale

MarketPulse talks to one provider. LiteLLM's value is proportional to how
many providers you use and how much you need its routing; at one provider,
most of what it carries is surface area that has to be kept working.

The parts actually needed — a catalog that keeps the picker and the cost
table from drifting, a fallback to a *different* model, errors classified
into retryable and not — are small, and writing them means they fit this
codebase's error taxonomy instead of sitting beside it.

## Consequences

Adding a second provider means writing an adapter rather than changing a
config line. That is the trade, and it is the right one at one provider; it
would be the wrong one at four.

The registry deliberately accepts model ids it does not know, so an operator
can move ahead of the catalog. Unknown models work and simply report no cost
estimate.
