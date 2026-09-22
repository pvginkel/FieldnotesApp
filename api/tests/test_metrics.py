"""`GET /metrics`: the Prometheus exposition. Whether the post-time answer lands is counted as a
matched post and what the same reporter did next."""

from datetime import timedelta

DUPLICATE = "uv sync installs no workspace members"
REPO = "pvginkel/Example"


def post(api, auth, text=DUPLICATE, session="s-1", **fields):
    body = {"area": "uv", "category": "hint", "text": text, "repo": REPO, "session": session}
    return api.post("/observations", json=body | fields, headers=auth())


def react(api, auth, id_, session="s-1"):
    body = {"emoji": "👍", "repo": REPO, "session": session}
    return api.post(f"/observations/{id_}/reactions", json=body, headers=auth())


def test_metrics_answer_without_a_token_and_count_the_store(start, auth, scrape):
    with start() as api:
        post(api, auth)
        post(api, auth, text="the relay drops deliveries", category="friction")
        response = api.get("/metrics")
        value = scrape(api)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert value("fieldnotes_observations", status="open", category="hint") == 1
    assert value("fieldnotes_observations", status="open", category="friction") == 1
    assert value("fieldnotes_observations", status="closed", category="idea") == 0
    assert value("fieldnotes_store_reactions", repo=REPO, emoji="📝") == 2


def test_every_closed_label_set_is_there_from_the_start(start, scrape):
    """A series born at 1 is an increment `increase()` never sees."""
    with start() as api:
        text = api.get("/metrics").text

    for line in (
        'fieldnotes_posts_total{category="idea",client="skills",outcome="forced"} 0.0',
        'fieldnotes_match_follow_ups_total{result="reacted"} 0.0',
        'fieldnotes_post_candidates_total{match_class="related"} 0.0',
        'fieldnotes_reactions_total{client="mcp"} 0.0',
        'fieldnotes_webhook_deliveries_total{action="queued",source="github"} 0.0',
        'fieldnotes_board_syncs_total{result="failed"} 0.0',
    ):
        assert line in text


def test_posts_are_counted_by_outcome(start, auth, scrape):
    with start() as api:
        post(api, auth)
        post(api, auth)
        post(api, auth, force=True)
        value = scrape(api)

    for outcome in ("created", "matched", "forced"):
        labels = {"client": "mcp", "category": "hint", "outcome": outcome}
        assert value("fieldnotes_posts_total", **labels) == 1, outcome
    assert value("fieldnotes_post_candidates_total", match_class="likely") == 1
    assert value("fieldnotes_post_top_score_count") == 1
    assert value("fieldnotes_match_follow_ups_total", result="forced") == 1


def test_a_reaction_to_an_offered_candidate_is_a_landed_answer(start, auth, scrape):
    with start() as api:
        id_ = post(api, auth).json()["id"]
        other = post(api, auth, text="the relay drops deliveries").json()["id"]
        post(api, auth)
        react(api, auth, id_)
        post(api, auth, session="s-2")
        react(api, auth, other, session="s-2")
        react(api, auth, other, session="s-3")
        value = scrape(api)

    assert value("fieldnotes_match_follow_ups_total", result="reacted") == 1
    assert value("fieldnotes_match_follow_ups_total", result="reacted_other") == 1
    assert value("fieldnotes_reactions_total", client="mcp") == 3
    assert value("fieldnotes_store_reactions", repo=REPO, emoji="👍") == 3


def test_a_matched_post_left_alone_is_abandoned_and_a_second_is_a_repost(
    start, auth, scrape, clock
):
    with start() as api:
        post(api, auth)
        post(api, auth)
        post(api, auth)
        assert scrape(api)("fieldnotes_match_follow_ups_total", result="reposted") == 1
        clock.now += timedelta(minutes=31)
        value = scrape(api)

    assert value("fieldnotes_match_follow_ups_total", result="abandoned") == 1


def test_gets_and_requests_are_counted_by_route(start, auth, scrape):
    with start() as api:
        id_ = post(api, auth).json()["id"]
        api.get(f"/observations/{id_}", headers=auth())
        value = scrape(api)

    assert value("fieldnotes_gets_total", client="mcp") == 1
    labels = {"method": "GET", "route": "/observations/{id}", "status": "200"}
    assert value("fieldnotes_http_request_duration_seconds_count", **labels) == 1
