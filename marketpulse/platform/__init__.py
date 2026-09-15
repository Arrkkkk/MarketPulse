"""Cross-cutting infrastructure: logging, HTTP resilience, caching.

The bottom layer. Nothing in here may import from `marketpulse.providers`,
`marketpulse.services` or the UI — the dependency arrow points only inward.
"""
