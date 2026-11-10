"""Account-agnostic exposure extraction layer.

Turns arbitrary submission JSON (cell-level spreadsheet extractions,
nested ACORD form dumps, unfamiliar schemas) into the canonical,
evidence-linked data model used by the rule engine and LLM reviewers.

Layers:
- cellgrid: deterministic parser for cell-level workbook extractions
  (``sheets[].all_nonempty_rows[].cells[]`` shape) with real
  sheet/row/cell citations.
- concepts: generic header/measure alias maps from extraction output to
  canonical insurance concepts. Aliases are domain vocabulary, never
  account-specific values.
- llm_extractor: LLM-based structure identification and exposure
  extraction for documents the deterministic layer cannot type, with
  deterministic citation validation.
- judge: LLM-as-judge validation of extracted exposure models.
- store: persistence of extracted exposure models as JSON.

The LLM extractor proposes source paths only; deterministic validation
resolves them against original input, then the judge gates accepted values.
"""
