"""JUnit XML reports: a suite's passed, failed and skipped counts.

pytest (``--junitxml``) and Playwright (the ``junit`` reporter) both write
one. The suite runner reads them for its summary and test_results.md, and
for the ``===SUITE_RESULT:===`` markers CI parses, so the two always agree.
"""

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import NamedTuple


class Counts(NamedTuple):
    passed: int
    failed: int
    skipped: int


def read_counts(xml_file: Path) -> Counts:
    """The counts in one JUnit XML file. Failed counts failures and errors.

    Raises ``ET.ParseError`` or ``ValueError`` for a file that isn't a
    readable report.
    """
    root = ET.parse(xml_file).getroot()
    # For a <testsuites> root, aggregate across all <testsuite> children
    # (Playwright emits one per spec file). Sum children to stay robust
    # against reporters that only populate per-suite counts.
    suites = root.findall("testsuite") if root.tag == "testsuites" else [root]
    tests = sum(int(s.get("tests", 0)) for s in suites)
    failures = sum(int(s.get("failures", 0)) for s in suites)
    errors = sum(int(s.get("errors", 0)) for s in suites)
    skipped = sum(int(s.get("skipped", 0)) for s in suites)
    return Counts(tests - failures - errors - skipped, failures + errors, skipped)


def report_counts(xml_file: Path) -> Counts | None:
    """The counts in a step's report, or None when the step wrote none."""
    try:
        return read_counts(xml_file)
    except (OSError, ET.ParseError, ValueError):
        return None


def format_counts(counts: Counts | None) -> str:
    if counts is None:
        return "no report"
    return f"{counts.passed} passed, {counts.failed} failed, {counts.skipped} skipped"
