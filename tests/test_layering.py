"""Only the data layer imports vendor libraries.

Vendor calls belong in dataflows, where failures are raised as VendorError
subclasses; a call made elsewhere can report an outage as a fact about the market.
"""

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
VENDOR_LIBRARIES = {"yfinance"}


def _imports(path: Path) -> set[str]:
    names = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            names.add(node.module.split(".")[0])
    return names


@pytest.mark.unit
def test_vendor_libraries_are_imported_only_by_the_data_layer():
    # The research package is the evidence layer's own data layer: its providers
    # and collectors record every vendor failure as an explicit evidence issue or
    # withheld value, never as a fact about the market.
    data_layers = (ROOT / "tradingagents" / "dataflows", ROOT / "tradingagents" / "research")
    offenders = sorted(
        str(path.relative_to(ROOT))
        for package in ("tradingagents", "cli")
        for path in (ROOT / package).rglob("*.py")
        if not any(layer in path.parents for layer in data_layers) and _imports(path) & VENDOR_LIBRARIES
    )
    assert offenders == []
