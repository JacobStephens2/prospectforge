"""Eval harness — spec §14.

Hand-labeled fixtures live in `fixtures/*.yaml`. Each fixture has:
  - `page_text`: the raw source text the extractor would receive
  - `source_url`: a representative URL (passed to the prompt)
  - `expected`: ground-truth contacts (name + at least one of email/title)

The CLI (`python -m app.eval.run`) runs extract_from_page over every fixture
and reports precision/recall/F1 per the spec's success criteria:
  - Extraction precision ≥ 85% by week 8
  - Approval rate ≥ 60% (subjective; not measurable from fixtures alone)

Adding fixtures is the operator's job (Morgan + Jacob co-label 5 during week 1
per §14.1). The starter fixture matches our existing test page so the harness
is exercised end-to-end on day one.
"""
