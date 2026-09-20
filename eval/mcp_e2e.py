"""The MCP end-to-end (design, "Validation"; `docs/slice-test-plan.md` §2, row 7): a scripted MCP
client drives `post`, `react` and `get` against a running `fieldnotes-mcp`, over its
Streamable-HTTP transport, the way an agent does.

    cexec python uv run --all-packages python eval/mcp_e2e.py            # the local server
    cexec python uv run --all-packages python eval/mcp_e2e.py \
        --url http://fieldnotes-mcp.home/mcp --token "$TOKEN"            # the deployment

The token is `--token` or `FIELDNOTES_MCP_TOKEN`; the deployment's is the OpenBao leaf
`eso/prd/fieldnotes/prd/mcp-token#token`.

In order: the bearer gate refuses a call without the token and with a wrong one; the tool surface
is exactly `get`, `post` and `react`; a novel post creates an observation; a paraphrase of it from
another repo creates nothing and comes back as a `likely` candidate with its reaction counts and
its next step; a reaction on that candidate is appended with its provenance and moves `repos` and
`last_seen`; the same paraphrase with `force` creates a second observation; and an unknown id and
the `bug` category are tool errors that say why.

Posting is the check, so the observations it writes stay in the store, which for the deployment is
the real one. Every text is invented and names a tool that does not exist, so the test data is
unmistakable; each run takes the first scenario the store does not hold already, so the script can
be run again until the pool runs out.

The exit status is 1 when a check fails, naming the one that did.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import secrets
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import httpx
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult

from fieldnotes_contracts import (
    POST_EMOJI,
    Candidate,
    MatchClass,
    Observation,
    PostReply,
    ReactReply,
)

DEFAULT_URL = "http://localhost:8766/mcp"
TOKEN_ENV = "FIELDNOTES_MCP_TOKEN"

# FR-6: exactly these, and no search.
TOOLS = ["get", "post", "react"]

# The two repos the run reports from, invented, so the store's test data is easy to find again.
FIRST_REPO = "fieldnotes-e2e/alpha"
SECOND_REPO = "fieldnotes-e2e/beta"

EMOJI = "👍"

# A well-formed id (26 characters of Crockford base32) no observation can carry.
UNKNOWN_ID = "0" * 26

# The bearer gate answers before the transport does, so any body will do; this is the one a client
# opens with.
INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "mcp_e2e", "version": "0"},
    },
}
ACCEPT = {"Accept": "application/json, text/event-stream"}


@dataclass(frozen=True)
class Scenario:
    """One invented observation: what is posted, the paraphrase that should match it, and the
    reaction the second reporter adds."""

    area: str
    text: str
    paraphrase: str
    reaction: str


SCENARIOS = [
    Scenario(
        area="kelp CLI",
        text="kelp sync leaves .kelp/lock behind when a run is interrupted, and the run after it "
        "blocks on that lock for the whole --lock-wait, 10 minutes by default, before failing "
        "with `kelp: another sync holds the lock`. There is no `kelp unlock`: delete .kelp/lock "
        "by hand and run the sync again.",
        paraphrase="An interrupted `kelp sync` does not clean up .kelp/lock, so the next sync "
        "waits out the full 10-minute --lock-wait and then dies with `kelp: another sync holds "
        "the lock`. Removing .kelp/lock yourself is the only way on; kelp ships no unlock "
        "subcommand.",
        reaction="A SIGKILL of the whole process group leaves the same lock behind.",
    ),
    Scenario(
        area="brindle chart linter",
        text="brindle lint resolves a values file through its symlink and reports "
        "`values.yaml: not a regular file`, so a chart whose values are symlinked into place "
        "cannot be linted at all. Pass --values with the link's target, or copy the file into "
        "the chart.",
        paraphrase="A symlinked values.yaml fails `brindle lint` with `values.yaml: not a "
        "regular file`: the linter refuses anything that is not a regular file. Point --values "
        "at what the link points at, or copy the file in, and the lint passes.",
        reaction="A symlinked chart directory fails the same way, with the directory's name.",
    ),
    Scenario(
        area="marlin test runner",
        text="marlin keys its cached test virtualenv on the lock file and nothing else, so "
        "moving the source of an editable dependency leaves the old path in the venv and every "
        "import of it raises ModuleNotFoundError. Touching the lock file does not invalidate the "
        "cache; `marlin --rebuild-env` does.",
        paraphrase="The cache key for marlin's test venv is the hash of the lock file alone. "
        "Move an editable dependency's source and the stale path survives the next run: imports "
        "raise ModuleNotFoundError until `marlin --rebuild-env`. Changing the lock file's mtime "
        "changes nothing.",
        reaction="Renaming the package directory is the same trap; CI never sees it because its "
        "cache starts empty.",
    ),
    Scenario(
        area="pelagic metrics agent",
        text="pelagic stamps every sample in the host's local time while the dashboards query in "
        "UTC, so each panel is off by the host's offset and an alert looks like it fired an hour "
        "early after a DST change. pelagic.yaml has no timezone key: set TZ=UTC in the agent's "
        "unit file.",
        paraphrase="Samples from pelagic carry local timestamps rather than UTC, so a dashboard "
        "reading UTC shifts every point by the host's offset and an alert appears at the wrong "
        "hour once DST moves. There is nothing in pelagic.yaml for it; TZ=UTC in the unit file "
        "is the fix.",
        reaction="The agent reads TZ once at startup, so the unit needs a restart, not a reload.",
    ),
    Scenario(
        area="driftwood scheduler",
        text="driftwood reruns a job whose worker exits non-zero, three times over, and there is "
        "no way to mark one job non-retryable: `--retries 0` is a property of a whole queue. A "
        "handler that writes without an idempotency key duplicates everything it wrote before it "
        "failed.",
        paraphrase="A non-zero exit makes driftwood retry the job up to three times, and the "
        "per-job opt-out does not exist, only the per-queue `--retries 0`. Guard the handler "
        "with a run id of its own or the retry writes its rows a second time.",
        reaction="The retry keeps the original job id, so a log grep cannot tell the two runs "
        "apart either.",
    ),
    Scenario(
        area="tidepool queue",
        text="tidepool drops a message over 1 MiB and answers the publisher 202 anyway: the size "
        "check runs in the broker, after the ack, and its only trace is `payload too large` in "
        "the broker's log. Raise the topic's max_message_bytes, or publish a pointer and let the "
        "consumer fetch the body.",
        paraphrase="Anything larger than 1 MiB never arrives through tidepool, and the publisher "
        "still gets its 202 — the broker checks the size after acking and logs `payload too "
        "large` where nobody looks. Either lift max_message_bytes on the topic or send a "
        "reference instead of the payload.",
        reaction="The broker's log line names the topic but not the message id, so a dropped "
        "message cannot be traced back to its publisher.",
    ),
    Scenario(
        area="mooring secret syncer",
        text="mooring backs an errored secret off to its refresh interval, an hour by default, "
        "so correcting the value in the vault does not reach the cluster until the next cycle. "
        "`mooring refresh --secret <name>` forces it; without the force the pod keeps the old "
        "value and its next restart still fails.",
        paraphrase="Once mooring has failed to read a secret it will not try again before the "
        "refresh interval comes round, an hour later, however long the vault has held the right "
        "value. Force the cycle with `mooring refresh --secret <name>` or the cluster goes on "
        "serving the stale one.",
        reaction="The backoff is per secret, so one bad leaf does not hold up the others in the "
        "same store.",
    ),
    Scenario(
        area="halyard branch protection",
        text="halyard answers 200 with an empty body when the token lacks the repo:admin scope, "
        "instead of 403, so a script that reads the status alone reports a protection rule it "
        "never created. Assert on the rule id in the response body, and give the token the admin "
        "scope.",
        paraphrase="A token without repo:admin makes halyard reply 200 and an empty body rather "
        "than refusing, so branch protection looks applied and is not. Check the body for the "
        "rule id, not just the status, and widen the token's scope.",
        reaction="Deleting a rule with the same token is a 200 and an empty body too.",
    ),
]


class CheckFailed(RuntimeError):
    """A check did not hold. Everything after it is unproven, so the run stops here."""


def require(condition: object, what: str) -> None:
    if not condition:
        raise CheckFailed(what)


def step(message: str) -> None:
    print(f"ok  {message}")


def error_text(result: CallToolResult) -> str:
    return "".join(block.text for block in result.content if block.type == "text")


@asynccontextmanager
async def mcp_session(url: str, token: str, timeout: float) -> AsyncIterator[ClientSession]:
    """An initialized MCP client session with the server at `url`, presenting the bearer."""
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(headers=headers, timeout=timeout) as http:
        async with streamable_http_client(url, http_client=http) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session


async def call(session: ClientSession, tool: str, arguments: dict[str, Any]) -> Any:
    """A tool's structured result; a tool error is a failed check."""
    result = await session.call_tool(tool, arguments)
    require(not result.isError, f"{tool} failed: {error_text(result)}")
    require(result.structuredContent is not None, f"{tool} answered no structured content")
    return result.structuredContent


async def refused(session: ClientSession, tool: str, arguments: dict[str, Any]) -> str:
    """The text of the tool error `tool` is expected to answer with."""
    result = await session.call_tool(tool, arguments)
    require(result.isError, f"{tool} was expected to refuse {arguments} and did not")
    return error_text(result)


async def check_the_bearer_gate(url: str, token: str) -> None:
    """NFR-4: the token is the server's boundary."""
    async with httpx.AsyncClient(timeout=30) as http:
        for what, headers in [
            ("without a bearer", {}),
            ("with a wrong bearer", {"Authorization": f"Bearer {token}x"}),
        ]:
            response = await http.post(url, json=INITIALIZE, headers={**ACCEPT, **headers})
            require(
                response.status_code == 401,
                f"{what}: {response.status_code}, expected 401",
            )
            require(
                response.headers.get("www-authenticate") == "Bearer",
                f"{what}: no WWW-Authenticate: Bearer",
            )
            require(
                response.json().get("type") == "unauthenticated",
                f"{what}: {response.text}",
            )
    step("the bearer gate refuses a call without the token and with a wrong one")


async def post_novel(session: ClientSession, provenance: dict[str, str]) -> tuple[Scenario, str]:
    """The first scenario the store does not hold already, posted: the scenario and the id it
    created. A post answered with candidates created nothing, so trying the next one costs the
    store nothing but the match."""
    for scenario in SCENARIOS:
        reply = PostReply.model_validate(
            await call(
                session,
                "post",
                {
                    "area": scenario.area,
                    "category": "hint",
                    "text": scenario.text,
                    **provenance,
                },
            )
        )
        if reply.id:
            return scenario, reply.id
        held = reply.candidates[0]
        print(f"..  {scenario.area}: the store holds it already ({held.id}); trying the next")
    raise CheckFailed(
        f"all {len(SCENARIOS)} scenarios are in the store already, one per run of this script; "
        "the plan's step 8 clears that test data"
    )


def check_the_candidate(candidate: Candidate, created: str) -> None:
    require(
        candidate.id == created,
        f"the best candidate is {candidate.id}, expected the post just created, {created}",
    )
    require(
        candidate.match_class is MatchClass.likely,
        f"the candidate is {candidate.match_class}, not likely (score {candidate.score:.3f})",
    )
    require(
        f"{POST_EMOJI} (1)" in candidate.reactions,
        f"the candidate's reaction counts are {candidate.reactions}, expected the creating post",
    )
    require(
        created in candidate.next_step,
        f"the candidate's next step does not name it: {candidate.next_step!r}",
    )


async def end_to_end(session: ClientSession) -> list[str]:
    """The scripted sequence. Returns the ids it wrote into the store."""
    tools = sorted(tool.name for tool in (await session.list_tools()).tools)
    require(tools == TOOLS, f"the tools are {tools}, expected {TOOLS}")
    step(f"the tool surface is exactly {', '.join(TOOLS)}")

    reporter = f"mcp-e2e-{secrets.token_hex(4)}"
    scenario, created = await post_novel(session, {"repo": FIRST_REPO, "session": reporter})
    step(f"a novel post created {created} ({scenario.area}, reported from {FIRST_REPO})")

    paraphrase = {
        "area": scenario.area,
        "category": "hint",
        "text": scenario.paraphrase,
        "repo": SECOND_REPO,
        "session": reporter,
    }
    reply = PostReply.model_validate(await call(session, "post", paraphrase))
    require(
        reply.id is None,
        f"the paraphrase created {reply.id} instead of matching {created}",
    )
    candidate = reply.candidates[0]
    check_the_candidate(candidate, created)
    step(
        f"the paraphrase created nothing and came back as a likely candidate: score "
        f"{candidate.score:.3f}, cosine {candidate.cosine:.3f}, reactions {candidate.reactions}"
    )

    reacted = ReactReply.model_validate(
        await call(
            session,
            "react",
            {
                "id": created,
                "emoji": EMOJI,
                "text": scenario.reaction,
                "repo": SECOND_REPO,
                "session": reporter,
            },
        )
    )
    require(reacted.id == created, f"the reaction answered for {reacted.id}, not {created}")
    require(
        f"{EMOJI} (1)" in reacted.reactions and f"{POST_EMOJI} (1)" in reacted.reactions,
        f"the counts after the reaction are {reacted.reactions}",
    )
    step(f"the reaction was appended: {reacted.reactions}")

    observation = Observation.model_validate(await call(session, "get", {"id": created}))
    entry = next((one for one in observation.reactions if one.emoji == EMOJI), None)
    require(entry is not None, f"the {EMOJI} reaction is not in the observation")
    assert entry is not None  # for the type checker; `require` raised
    require(
        (entry.repo, entry.session, entry.client) == (SECOND_REPO, reporter, "mcp"),
        f"the reaction's provenance is {entry.repo}, {entry.session}, {entry.client}",
    )
    require(entry.text == scenario.reaction, f"the reaction's text is {entry.text!r}")
    require(
        observation.repos == [FIRST_REPO, SECOND_REPO],
        f"the observation's repos are {observation.repos}",
    )
    # The reaction's own time, not "after `created`": a post and the reaction to it can fall in
    # the same second, and do whenever the run does not straddle one.
    require(
        observation.last_seen == entry.at >= observation.created,
        f"last_seen {observation.last_seen} is not the reaction's time {entry.at}",
    )
    step(
        f"get shows the reaction with its provenance, repos {observation.repos} and last_seen "
        f"moved to {observation.last_seen:%Y-%m-%dT%H:%M:%SZ}"
    )

    forced = PostReply.model_validate(await call(session, "post", {**paraphrase, "force": True}))
    require(forced.id is not None, "the forced post created nothing")
    require(forced.id != created, "the forced post answered with the id it matched")
    require(not forced.candidates, f"the forced post also answered candidates: {forced.candidates}")
    assert forced.id is not None  # for the type checker; `require` raised
    second = Observation.model_validate(await call(session, "get", {"id": forced.id}))
    require(second.canonical, f"the forced observation {forced.id} has no statement")
    step(f"the same paraphrase with force created {forced.id}, and get reads it back")

    message = await refused(session, "get", {"id": UNKNOWN_ID})
    require("not-found" in message, f"an unknown id answered: {message}")
    step(f"an unknown id is a tool error that says why: {message}")

    message = await refused(session, "post", {**paraphrase, "category": "bug"})
    require("category" in message, f"the bug category answered: {message}")
    step("FR-7: the bug category is refused before the API is called")

    return [created, forced.id]


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--url", default=DEFAULT_URL, help=f"the /mcp endpoint ({DEFAULT_URL})")
    parser.add_argument(
        "--token",
        default=os.environ.get(TOKEN_ENV),
        help=f"the bearer agents present; ${TOKEN_ENV} otherwise",
    )
    parser.add_argument("--timeout", type=float, default=120.0, help="seconds for one call")
    args = parser.parse_args()
    if not args.token:
        parser.error(f"a token is needed: --token or ${TOKEN_ENV}")

    print(f"driving {args.url}")
    failed: CheckFailed | None = None
    written: list[str] = []
    try:
        await check_the_bearer_gate(args.url, args.token)
        async with mcp_session(args.url, args.token, args.timeout) as session:
            try:
                written = await end_to_end(session)
            except CheckFailed as failure:
                # Caught inside the session, because a failure crossing its task group comes back
                # wrapped in an exception group and the traceback buries the check that failed.
                failed = failure
    except CheckFailed as failure:
        failed = failure
    if failed is not None:
        print(f"FAILED  {failed}", file=sys.stderr)
        return 1

    print()
    print(f"the MCP end-to-end passed; it left {', '.join(written)} in the store")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
