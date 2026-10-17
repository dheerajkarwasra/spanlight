"""Accuracy harnesses run against a local Spanlight stack.

`accuracy.doctor` measures the Doctor's detectors on labelled scenarios (see its README). The
harnesses keep no credentials or state in the repository: state lives at the path named by
`DOCTOR_HARNESS_STATE` (see `accuracy.paths`) and the Anthropic key comes from
`ANTHROPIC_API_KEY`.
"""
