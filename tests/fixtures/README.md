# Test fixtures

## `kis_api_spec.xlsx`

**Source**: 한국투자증권 (Korea Investment & Securities) Open API spec workbook,
distributed from the [KIS Developers Portal](https://apiportal.koreainvestment.com)
to registered developers.

**Snapshot date**: 2026-05-24 (filename hashes
`54bda0d9-...20260524_030000.xlsx` / `941c3249-...20260524_030000.xlsx`,
MD5 `01285288ea61a2e8d09e840efe07ba94`)

**Why committed**: `tests/test_spec_compliance.py` parses 339 sheets of this
workbook to verify that `tests/mock_kis_server.py` responses contain every
field documented in the KIS spec. Committing the file lets the contract
suite run in CI; otherwise the tests skip when the spec isn't present.

**Refreshing**: Re-download the latest spec workbook from the portal and
overwrite this file. If KIS adds, removes, or renames fields, the
parametrized tests in `test_spec_compliance.py` will fail-loud and point
at the exact endpoint with the diff. Update `tests/mock_kis_server.py`
field manifests (`_BALANCE_OUTPUT1_FIELDS`, etc.) to match.

**Override**: For ad-hoc verification against a different spec snapshot,
set `KIS_API_SPEC_XLSX=/absolute/path/to/spec.xlsx` before running pytest.
