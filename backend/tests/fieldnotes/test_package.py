from pathlib import Path

import fieldnotes_contracts

BACKEND = Path(__file__).resolve().parents[2]


def test_the_contracts_are_the_workspace_source():
    # A live workspace source, not a built copy: an edit to the models is seen without a re-sync.
    assert Path(fieldnotes_contracts.__file__).is_relative_to(BACKEND / "packages")
