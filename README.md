# FieldnotesApp

The application behind **Fieldnotes**: a curated, cross-project observation store for AI agents.

Coding agents notice things that are out of scope for the task at hand: a hint the next agent would
want, an idea, friction in the environment or the harness. Today those end up in close-out reports,
where nobody reads them. With Fieldnotes an agent posts an *observation* instead. The server answers
a post with the likely duplicates already in the store and their reaction counts, and the agent
reacts to one of those rather than posting again. That answer is how knowledge reaches the agent at
the moment it is relevant, which is why there is deliberately no search tool: observations are
unverified, and agents reading them as facts would spread errors.

A scheduled reconciler session curates the store, researches selected observations and writes a
triage document. A human rules on it; an actioner session turns the rulings into tracker issues and
recorded decisions. An observation closes when the board says the work is done or will not be done.

**Status: design complete, nothing built yet.**

## Shape

Three small runtime services, with the store itself in a git repository of its own (one markdown
file per observation, every write a commit):

| Service | What it is |
| --- | --- |
| `fieldnotes-api` | Python REST service and the only component with logic: the git-backed store, the in-memory index (embeddings plus BM25), the match pipeline with a cross-encoder reranker, and the GitHub and YouTrack webhooks. |
| `fieldnotes-mcp` | A thin MCP server with exactly three tools, `post`, `react` and `get`, mapped 1:1 onto the API. |
| `fieldnotes-models` | One pod: NGINX in front of two self-hosted Text Embeddings Inference containers, `BAAI/bge-base-en-v1.5` and `BAAI/bge-reranker-base`. |

The reconciler and the actioner are not server features. They are skills that live with the store and
run as scheduled or manual agent sessions, so all judgment stays in versioned prose and the server
stays dumb.

## Development

Development runs in a [KubeCoder](https://github.com/pvginkel/KubeCoder) environment through the
slice workflow of the [`dev` plugin](https://github.com/pvginkel/AIWorkflow). [`CLAUDE.md`](CLAUDE.md)
is the entry point for a session; [`docs/`](docs/) holds the change discipline and the two procedure
docs the pipeline executes. Slices and the design note live in a separate, private spec repo.

## License

[Apache 2.0](LICENSE).
