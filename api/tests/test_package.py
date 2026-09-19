from pathlib import Path

import fieldnotes_api
import fieldnotes_contracts

REPO = Path(__file__).resolve().parents[2]


def test_importable_with_contracts_from_the_workspace():
    assert fieldnotes_api.__name__ == "fieldnotes_api"
    # A live workspace source, not a built copy: an edit to the models is seen without a re-sync.
    assert Path(fieldnotes_contracts.__file__).is_relative_to(REPO / "packages")
