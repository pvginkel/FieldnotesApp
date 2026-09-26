"""The wire models validate at the boundary: FR-1's post, FR-4's reaction, FR-7's fields."""

import pytest
from pydantic import ValidationError

from fieldnotes_contracts import (
    Candidate,
    Category,
    MatchClass,
    MatchRequest,
    PostReply,
    PostRequest,
    Problem,
    ReactRequest,
    Status,
)


def post(**overrides):
    fields = {"area": "uv workspace", "category": "hint", "text": "Sync with --all-packages."}
    fields["repo"] = "pvginkel/Example"
    return PostRequest(**{**fields, **overrides})


def candidate(id_="01J00000000000000000000000"):
    return Candidate(
        id=id_,
        area="uv workspace",
        canonical="Sync with --all-packages.",
        status=Status.open,
        outcome=None,
        pointer=None,
        reason=None,
        reactions=["📝 (1)"],
        cosine=0.9,
        score=0.8,
        match_class=MatchClass.likely,
        next_step="react",
    )


def test_post_strips_and_defaults():
    request = post(area="  uv workspace  ")
    assert request.area == "uv workspace"
    assert request.category is Category.hint
    assert request.session is None
    assert request.force is False


@pytest.mark.parametrize("field", ["area", "text", "repo"])
def test_post_refuses_a_blank_required_field(field):
    with pytest.raises(ValidationError):
        post(**{field: "   "})


def test_post_refuses_bug_as_a_category():
    # FR-7: product bugs are not observations.
    with pytest.raises(ValidationError):
        post(category="bug")


def test_post_refuses_an_unknown_field():
    with pytest.raises(ValidationError):
        post(severity="high")


def test_post_refuses_an_overlong_text():
    with pytest.raises(ValidationError):
        post(text="x" * 10_001)


def test_post_reply_is_an_id_or_candidates():
    assert PostReply(id="01J00000000000000000000000").candidates == []
    assert PostReply(candidates=[candidate()]).id is None
    with pytest.raises(ValidationError):
        PostReply()
    with pytest.raises(ValidationError):
        PostReply(id="01J00000000000000000000000", candidates=[candidate()])


def test_react_takes_any_emoji_and_optional_text():
    # FR-4: uncurated, 👎 allowed.
    request = ReactRequest(emoji="👎", repo="pvginkel/Example")
    assert request.text is None
    with pytest.raises(ValidationError):
        ReactRequest(emoji="", repo="pvginkel/Example")


@pytest.mark.parametrize("k", [0, 13])
def test_match_bounds_k_by_the_candidate_cap(k):
    with pytest.raises(ValidationError):
        MatchRequest(text="anything", k=k)


def test_problem_carries_extension_members():
    problem = Problem(type="validation-error", title="t", status=422, errors=[{"loc": ["body"]}])
    assert problem.model_dump(exclude_none=True)["errors"] == [{"loc": ["body"]}]
