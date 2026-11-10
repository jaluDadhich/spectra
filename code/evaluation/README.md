# Evaluation assets — ISOLATED FROM RUNTIME

`ground_truth/` contains expected issue labels per synthetic account. These
are evaluation-only answer keys. The runtime review path
(`app/agents/orchestrator.py`, rules, LLM tasks) never reads this directory;
only `spectra evaluate` and tests under `tests/` may load them. Injecting
these labels into runtime detection would invalidate the evaluation.
