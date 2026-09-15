# 3. No RAG, no vector store

**Status:** Accepted

## Context

The obvious move for "AI over documents" is retrieval-augmented generation:
embed the corpus, store the vectors, retrieve the top-k, put them in the
prompt. It is also the thing most reviewers expect to see in an AI project.

## Decision

No embeddings, no vector database, no retrieval step.

## Rationale

The corpus is five news articles, fetched fresh per request, none older than
seven days. They fit in the prompt with room to spare — a two-article
analysis measured 309 prompt tokens against a context window orders of
magnitude larger.

Retrieval solves the problem of having more context than fits. This project
does not have that problem. Adding RAG would introduce an embedding model, a
vector store to operate, an indexing pipeline to keep fresh, and a retrieval
step that can return the wrong articles — in exchange for selecting five
documents out of five.

## Consequences

Someone will ask why there is no RAG. The answer is that it would be
retrieval over a set small enough to pass whole, which costs latency,
infrastructure and a new failure mode for no benefit.

This decision would change if the product grew a corpus worth searching:
historical filings, transcripts, or a news archive spanning years. The
trigger is a corpus that no longer fits in context, not a desire to have
used the technique.
