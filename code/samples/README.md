# Samples — ALL SYNTHETIC

Every file under `samples/` is fabricated test data produced by
`generate_samples.py`. Companies ("Sierra Pacific Logistics LLC"), people,
VINs, FEINs, claim IDs, and external records are invented and do not
describe real entities.

- `clean/` — internally consistent submission; expected outcome **PASS**.
- `a01/` — manifested P2, P3, P6; non-manifested N3, N4, N5, N6, N7, N8,
  N14, N15.
- `a02/` — manifested P2, P4, P5, P7; non-manifested N3, N4, N6, N7, N8,
  N14, N15. N5 intentionally not independently evidenced.
- `a03/` — manifested P1, P3, P6, P7, P11; non-manifested N3, N4, N5, N6,
  N7, N8, N15. N14 intentionally covered by P7.

Evaluation labels live in `evaluation/ground_truth/` and are read only by
`spectra evaluate` and the test suite — never by the runtime review path.

Regenerate: `python samples/generate_samples.py`
