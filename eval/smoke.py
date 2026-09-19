"""Model smoke: the models pod answers, and answers sensibly (design, "Validation").

Embeds two paraphrases and one unrelated text and checks that the paraphrases sit closer together
than either does to the unrelated one. Then times embedding one text at observation length, the
match pipeline's only model call and so the model's share of NFR-1.

    cexec python uv run --all-packages python eval/smoke.py [--url URL]

Every text is invented. The exit status is 1 when the check fails. Timings are reported, not
judged.
"""

import argparse
import asyncio
import math
import statistics
import sys
import time

import httpx

DEFAULT_URL = "http://models.models-prd.svc.cluster.local"

# How many different texts a length is timed over.
TEXTS = 40

PARAPHRASE_A = (
    "uv workspace: running `uv sync` at the root of a workspace whose root project is not a "
    "package installs none of the members, so importing them fails; pass --all-packages."
)
PARAPHRASE_B = (
    "uv workspace: the member packages were missing from the virtualenv after a plain uv sync, "
    "because the root has package = false. Syncing with --all-packages fixes the "
    "ModuleNotFoundError."
)
UNRELATED = (
    "grafana: dashboard panels show times in the browser's timezone unless the dashboard sets "
    "one, so screenshots shared across timezones disagree about when an alert fired."
)

AREAS = [
    "test suite",
    "git workflow",
    "helm chart",
    "container image",
    "webhook handler",
    "embedding cache",
    "database migration",
    "logging",
]

SENTENCES = [
    "The integration suite leaves a stale lock file behind when a run is interrupted, and the "
    "next run waits on it until the timeout.",
    "Rebasing onto the moved base before pushing avoids a rejected push, but the rebase has to "
    "rerun the formatter.",
    "The chart renders fine locally yet fails the server-side apply, because the live object "
    "still carries a defaulted field.",
    "A probe with the default one-second timeout fails whenever the endpoint queues behind a "
    "slow request.",
    "Pinning the image by digest keeps a redeploy reproducible, and the poller still notices "
    "when the tag moves.",
    "The fixture generator writes timestamps in local time, so tests that compare dates fail "
    "after midnight UTC.",
    "Running the linter before the formatter reports line-length errors that the formatter would "
    "have fixed on its own.",
    "The webhook handler must answer quickly and queue the work, or the sender retries and the "
    "job runs twice.",
    "Caching embeddings by content hash means a rename costs nothing, while an edited statement "
    "is embedded again.",
    "The migration script assumes an empty table and silently skips rows that already exist.",
    "A token read from the environment at import time is missing in tests that set it later in "
    "a fixture.",
    "Two sessions writing the same working tree collide on the index lock; stage files by name "
    "and commit often.",
    "The container image ships without a shell, so debugging needs an ephemeral container "
    "attached to the pod.",
    "Log lines without the request id make it impossible to follow one call across the three "
    "services.",
    "The retry loop backs off exponentially but never caps the delay, so a long outage parks the "
    "worker for hours.",
    "Splitting the report on headings breaks when an entry quotes a heading inside a code block.",
]


def cosine(u: list[float], v: list[float]) -> float:
    dot = sum(a * b for a, b in zip(u, v, strict=True))
    return dot / (math.sqrt(sum(a * a for a in u)) * math.sqrt(sum(b * b for b in v)))


def observation(i: int, words: int) -> str:
    """An invented observation of at least `words` words, in the embedded-text shape
    `area: canonical`, worded differently for every `i`."""
    parts: list[str] = []
    count = 0
    j = i
    while count < words:
        sentence = SENTENCES[j % len(SENTENCES)]
        parts.append(sentence)
        count += len(sentence.split())
        j += 3
    return f"{AREAS[i % len(AREAS)]}: {' '.join(parts)}"


def p95(samples: list[float]) -> float:
    return statistics.quantiles(samples, n=20, method="inclusive")[18]


async def check(client: httpx.AsyncClient) -> bool:
    response = await client.post("/embed", json={"inputs": [PARAPHRASE_A, PARAPHRASE_B, UNRELATED]})
    response.raise_for_status()
    a, b, c = response.json()
    paraphrase = cosine(a, b)
    unrelated = max(cosine(a, c), cosine(b, c))
    embed_ok = paraphrase > unrelated
    print(f"cosine: paraphrase {paraphrase:.3f}, unrelated {unrelated:.3f}")

    print(f"embedding separates the paraphrase: {'yes' if embed_ok else 'NO'}")
    return embed_ok


async def time_calls(client: httpx.AsyncClient, words: int, repeats: int) -> list[float]:
    """Seconds per embedding of one text, a different text each round. One extra round runs
    first as a warm-up and is dropped."""
    samples: list[float] = []
    for round_ in range(repeats + 1):
        text = observation(round_ % TEXTS, words)
        started = time.perf_counter()
        (await client.post("/embed", json={"inputs": text})).raise_for_status()
        if round_:
            samples.append(time.perf_counter() - started)
    return samples


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--url", default=DEFAULT_URL, help=f"models pod (default {DEFAULT_URL})")
    parser.add_argument("--repeats", type=int, default=10, help="timed rounds per length")
    parser.add_argument(
        "--words",
        type=int,
        nargs="+",
        default=[40, 100, 200],
        help="observation lengths to time, in words",
    )
    args = parser.parse_args()

    async with httpx.AsyncClient(base_url=args.url, timeout=120) as client:
        ok = await check(client)

        print()
        print(f"One text embedded; {args.repeats} rounds per length, milliseconds:")
        print()
        print("| words | embed p50 | embed p95 |")
        print("| ---: | ---: | ---: |")
        for words in args.words:
            samples = await time_calls(client, words, args.repeats)
            print(
                f"| {words} | {statistics.median(samples) * 1000:.0f} | {p95(samples) * 1000:.0f} |"
            )

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
