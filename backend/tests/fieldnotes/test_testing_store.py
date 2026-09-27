"""The Playwright suite's store, /api/testing/store: laid out by a test, then read back. Every item
is invented."""

from app import create_app
from app.fieldnotes.triage import item_path
from tests.fieldnotes.test_triage import IDS, item, queue, ruling, spelled


def test_put_lays_out_the_store_and_the_queue_follows(start, remote):
    with start() as api:
        first = {item_path(IDS[0]): spelled(item(IDS[0])), item_path(IDS[1]): spelled(item(IDS[1]))}
        assert api.put("/api/testing/store", json={"files": first}).status_code == 204
        assert [i["observation"] for i in queue(api)["items"]] == IDS[:2]
        assert remote.files("triage") == [item_path(IDS[0]), item_path(IDS[1])]

        # The next layout replaces the last: what it leaves out is gone from the store and the queue.
        second = {item_path(IDS[2]): spelled(item(IDS[2], ruling=ruling("no", "not now")))}
        assert api.put("/api/testing/store", json={"files": second}).status_code == 204
        assert [i["observation"] for i in queue(api)["items"]] == [IDS[2]]
        assert remote.files("triage") == [item_path(IDS[2])]

        # The same layout again commits nothing.
        commits = remote.log()
        assert api.put("/api/testing/store", json={"files": second}).status_code == 204
        assert remote.log() == commits

        assert api.get("/api/testing/store").json() == {"files": second}


def test_put_refuses_a_path_outside_the_two_directories(start):
    with start() as api:
        response = api.put("/api/testing/store", json={"files": {"README.md": "no"}})
        assert response.status_code == 400
        assert api.put("/api/testing/store", json={"files": []}).status_code == 400


def test_outside_testing_mode_it_is_not_there(start, test_settings, test_app_settings):
    settings = test_settings.model_copy(update={"flask_env": "development"})
    app = create_app(settings, app_settings=test_app_settings, skip_background_services=True)
    with start(app=app) as api:
        assert api.get("/api/testing/store").status_code == 400
