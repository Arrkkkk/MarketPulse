# Architecture decision records

Short notes on decisions that were not obvious, written at the time they
were made. Each says what was chosen, what was rejected, and why.

The rejections matter more than the adoptions. Anyone can add a vector
database; the interesting question is why this project does not have one.

| # | Decision | Status |
|---|---|---|
| [0001](0001-fail-loud-provider-contract.md) | Providers raise on failure; empty means empty | Accepted |
| [0002](0002-service-and-thin-client.md) | FastAPI service with a thin Streamlit client | Accepted |
| [0003](0003-no-rag.md) | No RAG, no vector store | Accepted |
| [0004](0004-no-litellm.md) | Own LLM abstraction rather than LiteLLM | Accepted |
| [0005](0005-synchronous-platform-layer.md) | The platform layer is synchronous | Accepted |
| [0006](0006-caching-as-a-decorator.md) | Caching decorates the provider interface | Accepted |
| [0007](0007-lttb-downsampling.md) | LTTB rather than stride downsampling | Accepted |
| [0008](0008-json-logging-not-structlog.md) | A stdlib JSON formatter, not structlog | Accepted |
| [0009](0009-in-process-rate-limiting.md) | In-process rate limiting | Accepted, with a known limit |
| [0010](0010-null-model-pricing.md) | Model pricing stays null until verified | Accepted |
| [0011](0011-sparkline-payload.md) | A sparkline on the overview, stocks only | Accepted |
