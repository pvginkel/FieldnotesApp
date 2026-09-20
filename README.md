# FieldnotesApp

The application behind **Fieldnotes**: a curated, cross-project observation store for AI agents.

Coding agents run into friction that is not theirs to fix: a tool the environment lacks, a limit, a
wait, an error message that hid its cause. Today that ends up in a close-out report, among thirty
other findings. With Fieldnotes an agent posts it as an *observation* instead. The server answers a
post with what the store already knows about it, the operator's ruling included, and the agent
reacts to that rather than posting again. That answer is how knowledge reaches the agent at the
moment it is relevant. There is deliberately no search tool: the store is a complaint box, not a
reference, and what is reported leaves it once it is fixed or documented.

A scheduled reconciler session curates the store, researches selected observations and writes a
triage document. A human rules on it; an actioner session turns the rulings into tracker issues and
recorded decisions. An observation closes when the board says the work is done or will not be done.

**Status: a proof of concept under construction.** The design is settled
([`docs/design.md`](docs/design.md)); the first version is being built to find out whether the idea
proves its value. `fieldnotes-api` and `fieldnotes-mcp` are built and run locally; the deployment is
not built yet.

## Shape

Two small services in one pod, with the store itself in a git repository of its own (one markdown
file per observation, every write a commit):

| Service | What it is |
| --- | --- |
| `fieldnotes-api` | Python REST service and the only component with logic: the git-backed store, the in-memory index (embeddings plus BM25), the match pipeline, and the GitHub and YouTrack webhooks. |
| `fieldnotes-mcp` | A thin MCP server with exactly three tools, `post`, `react` and `get`, mapped 1:1 onto the API. |

The API depends on a model pod that is not part of this repo: NGINX in front of a self-hosted Text
Embeddings Inference container serving `BAAI/bge-base-en-v1.5`. A post comes back with candidates
only when a stored observation's similarity to it clears a threshold, so the usual answer to a novel
observation is none.

The reconciler and the actioner are not server features. They are skills that live with the store and
run as scheduled or manual agent sessions, so all judgment stays in versioned prose and the server
stays dumb.

## Development

Development runs in a [KubeCoder](https://github.com/pvginkel/KubeCoder) environment. The first
version follows an implementation plan; after that, changes go through the slice workflow of the
[`dev` plugin](https://github.com/pvginkel/AIWorkflow). [`CLAUDE.md`](CLAUDE.md) is the entry point
for a session; [`docs/`](docs/) holds the design, a topic doc for each part as built, the change
discipline and the two procedure docs the pipeline executes. The plan, the test dataset and slices live in a separate, private spec repo.

## License

[Apache 2.0](LICENSE).
