# Test questions per business

Questions to ask the assistant (voice app `/web/`, `chat.html`, or a Twilio call) to see
what it knows and how it behaves. The expected answers come from the crawled site as
saved in `tests/fixtures/` and from `tests/eval/`; the site can change, so a wrong-looking
answer may be a stale expectation. Re-check before treating it as a bug.

The scored subset lives in [`tests/eval/`](../tests/eval/) (`uv run python -m app.rag.eval ...`);
this folder is for asking by hand.

One file per business, named after it (e.g. [`bathhouse.md`](bathhouse.md) for
abathhouse.com). For a new business, copy [`_template.md`](_template.md) and fill in the
expected column from its site.
