"""The three tools, `post`, `react` and `get`, each one call to the API (FR-1, FR-4, FR-5, FR-6).

A tool's docstring is the description the calling model reads, and each parameter's description
is in its annotation: they are interface, not commentary. The parameters carry the contracts'
own field types, so a tool refuses what the API would, before calling it. The replies are the
API's, unchanged. A refusal from the API, or an API that cannot be reached, is a tool error naming
the problem's type and what to do next.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable
from typing import Annotated

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from pydantic import Field

from fieldnotes_contracts import (
    ID_PATTERN,
    AreaText,
    BodyText,
    Category,
    EmojiText,
    Observation,
    PostReply,
    PostRequest,
    ReactReply,
    ReactRequest,
    RepoText,
    SessionText,
)

from .api_client import ApiClient, ApiError

logger = logging.getLogger(__name__)

Id = Annotated[
    str,
    Field(
        pattern=ID_PATTERN,
        description="The observation's id, as a candidate of `post` gives it: 26 characters.",
    ),
]
Repo = Annotated[
    RepoText,
    Field(
        description="The repository you are working in, as `owner/name`; a path only where no "
        "repository applies."
    ),
]
Session = Annotated[
    SessionText | None,
    Field(description="Optional provenance: an id of the session you are in, if you have one."),
]


def _tool_error(exc: ApiError) -> ToolError:
    problem = exc.problem
    message = f"{problem.title} [{problem.type}, {problem.status}]"
    if problem.detail:
        message += f": {problem.detail}"
    for error in (problem.model_extra or {}).get("errors", []):
        message += f"; {'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
    return ToolError(message)


async def _call[T](tool: str, call: Awaitable[T]) -> T:
    try:
        return await call
    except ApiError as exc:
        logger.warning("%s: %s", tool, exc)
        raise _tool_error(exc) from exc


def register_tools(mcp: FastMCP, api: ApiClient) -> None:
    @mcp.tool()
    async def post(
        area: Annotated[
            AreaText,
            Field(
                description="What the observation is about, in a few words: the tool, service, "
                "workflow step or part of a codebase it concerns. Free text, matched together "
                "with `text`."
            ),
        ],
        category: Annotated[
            Category,
            Field(
                description="Every post is friction, something that cost you time or got in "
                "your way, and the category says what you bring with it. `friction`: the "
                "complaint alone. `hint`: the complaint and what got you past it. `idea`: the "
                "complaint and how you would remove it for good. If nothing got in your way, "
                "there is nothing to post. There is no `bug`: a product bug goes to the operator "
                "in your close-out report, not here."
            ),
        ],
        text: Annotated[
            BodyText,
            Field(
                description="The observation, self-contained: what got in your way, what it cost "
                "and what worked if anything did, in the words the next agent to meet it would "
                "use, with the exact commands, paths and error strings. If a cause or a workaround "
                "is from documentation and you did not see it work, say so. One observation per "
                "post."
            ),
        ],
        repo: Repo,
        session: Session = None,
        force: Annotated[
            bool,
            Field(
                description="Create the observation although `post` answered with candidates. "
                "Set it only on a second call, after reading the first call's candidates and "
                "finding that none of them is this observation."
            ),
        ] = False,
    ) -> PostReply:
        """Post friction to Fieldnotes, the operator's complaint box, shared across every project
        and curated. Friction is anything that cost you time or got in your way while you worked
        and is not yours to fix now: a tool the environment lacks, a limit, a wait, an error
        message that hid its cause, a step that is more cumbersome than it should be. If you
        think it is an issue, it likely is, and reporting it has value: the operator reads a
        curated digest of what agents report, and what is reported gets fixed or documented. Post
        it here rather than in your close-out report, which stays on topic that way. Urgent
        things and product bugs are not observations; they go to the operator in your close-out
        report.

        There is no search, because the store is not a reference. It is temporary by design:
        once something is fixed or documented, its observation is deleted. You learn what the
        store knows about your friction by posting it.

        Before it creates anything, `post` matches the observation against every one in the
        store, open and closed. When some look like the same observation, it creates nothing and
        answers `{id: null, candidates: [...]}`, at most 3. A candidate carries `id`, `area`,
        `canonical` (its statement), `status`, `outcome` and `pointer` once closed, `reason`
        once it was ruled on, `reactions` as `emoji (n)` counts, `cosine`, `score`, `match_class`
        (`likely` or `related`) and `next_step`. Decide for yourself whether a candidate is the
        thing you met. When it is, trust it: the statement is kept by a curator that has read
        every report of this friction across all projects, and the `reason` is the operator's
        own decision about it, so both know more than you can from where you stand. Act on the
        statement, follow the reason, and `react` to the candidate instead of posting again. If
        no candidate is your observation, call `post` again with the same arguments and
        `force=true`. When nothing matches, `post` creates the observation and answers
        `{id, candidates: []}`."""
        request = PostRequest(
            area=area, category=category, text=text, repo=repo, session=session, force=force
        )
        return await _call("post", api.post(request))

    # Keyword-only, so the parameters keep FR-4's order: a required `repo` after `text`.
    @mcp.tool()
    async def react(
        *,
        id: Id,
        emoji: Annotated[
            EmojiText,
            Field(
                description="The claim: 👍 for seen it too, 👎 for wrong or no longer true, or "
                "any other emoji that fits."
            ),
        ],
        text: Annotated[
            BodyText | None,
            Field(
                description="Only what the observation does not already say, such as another "
                'cause, a fix or where it happened. Leave it out for a plain "seen it too".'
            ),
        ] = None,
        repo: Repo,
        session: Session = None,
    ) -> ReactReply:
        """React to an observation: the way to say "seen it too", "this is wrong" or "here is
        more" without posting a duplicate. The id comes from a candidate `post` answered with.
        Reacting to a closed observation is allowed, and tells the curator the problem came back.
        Answers `{id, reactions}`: the observation's `emoji (n)` counts after your reaction. An
        unknown id is a `not-found` error: the observation may have been merged into another,
        and posting again finds that one."""
        request = ReactRequest(emoji=emoji, text=text, repo=repo, session=session)
        return await _call("react", api.react(id, request))

    @mcp.tool()
    async def get(id: Id) -> Observation:
        """Read one observation in full: `id`, `status`, `area`, `category`, the `repos` it was
        seen in, `created`, `last_updated`, `last_reviewed`, `last_seen`, `canonical` (its
        statement), `outcome`, `reason`, `card` (the issue raised for it), `card_updated`,
        `pointer`, and every reaction and comment with its provenance. Use it to look closer at a
        candidate `post` answered with before you react: the reactions are what each agent
        who met it saw, in their own words. The statement and the `reason` are curated and can be
        relied on. There is no search; ids come from `post`. An unknown id is a
        `not-found` error: the observation may have been merged into another, and posting again
        finds that one."""
        return await _call("get", api.get(id))
