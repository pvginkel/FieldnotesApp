"""Embed the dataset with a candidate embedding model, locally, for `eval/bench.py` to compare.

The models pod serves one embedder. Trying another through it means a chart change and a rollout
per candidate; this script runs the candidate under sentence-transformers instead and writes its
vectors in the API's embedding-cache layout (`<work>/cache/<model>/<sha256 of the embedded text>`,
float32, L2-normalised), where the bench finds them. Each model is loaded the way its card says
for symmetric similarity: sentence-transformers' defaults, which is no prompt for the BERT-class
models and `Document: ` on both sides for jina v5's text-matching models.

It also times one text at a time, the shape of a post. That is torch in fp32 on whatever else the
node is doing, not TEI's ONNX runtime: read the times against the served model's time in the same
run, not as what a deployment would measure.

sentence-transformers and torch are not workspace dependencies, and the `python` tool container
is too small to load the larger models. A venv of its own, made once, then run from the dev
container:

    cexec python uv venv --python /usr/bin/python3 .run/bench/venv
    cexec python uv pip install --python .run/bench/venv/bin/python \\
        --index-strategy unsafe-best-match \\
        --extra-index-url https://download.pytorch.org/whl/cpu torch sentence-transformers peft
    HF_HOME=.run/bench/hf .run/bench/venv/bin/python eval/embed_local.py \\
        --dataset ../FieldnotesAppSpecs/dataset --work .run/bench MODEL...
"""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import time
from pathlib import Path

import numpy as np
import torch
from sentence_transformers import SentenceTransformer

TIMED = 30  # texts timed one at a time, after a warm-up


def embedded_texts(dataset: Path) -> list[str]:
    """What the API embeds for each observation of the dataset: `area: text`."""
    lines = (dataset / "observations.jsonl").read_text().splitlines()
    items = [json.loads(line) for line in lines if line.strip()]
    return sorted({f"{item['area']}: {item['text']}" for item in items})


def main() -> int:
    parser = argparse.ArgumentParser(description="Embed the dataset with local models.")
    parser.add_argument("--dataset", type=Path, required=True, help="the dataset directory")
    parser.add_argument("--work", type=Path, required=True, help="the bench's work directory")
    parser.add_argument("models", nargs="+", metavar="MODEL", help="Hugging Face model ids")
    args = parser.parse_args()

    texts = embedded_texts(args.dataset)
    for name in args.models:
        started = time.perf_counter()
        # Some checkpoints are stored in half precision, and transformers loads what is stored:
        # fp16 on a CPU is many times slower than fp32, which is also what TEI's CPU image runs.
        model = SentenceTransformer(name, device="cpu", model_kwargs={"dtype": torch.float32})
        loaded = time.perf_counter() - started

        model.encode(texts[:2], normalize_embeddings=True)
        single = []
        for text in texts[:TIMED]:
            started = time.perf_counter()
            model.encode([text], normalize_embeddings=True)
            single.append(time.perf_counter() - started)

        started = time.perf_counter()
        vectors = model.encode(texts, batch_size=8, normalize_embeddings=True)
        whole = time.perf_counter() - started

        directory = args.work / "cache" / name
        directory.mkdir(parents=True, exist_ok=True)
        for text, vector in zip(texts, vectors, strict=True):
            key = hashlib.sha256(text.encode()).hexdigest()
            (directory / key).write_bytes(np.asarray(vector, dtype=np.float32).tobytes())

        timing = {
            "model": name,
            "dimensions": int(vectors.shape[1]),
            "parameters": sum(p.numel() for p in model.parameters()),
            "prompt": model.prompts.get(model.default_prompt_name or "", ""),
            "max_tokens": model.max_seq_length,
            "load_s": round(loaded, 1),
            "single_p50_ms": round(statistics.median(single) * 1000),
            "single_max_ms": round(max(single) * 1000),
            "all_texts_s": round(whole, 1),
            "texts": len(texts),
        }
        timings = args.work / "timing"
        timings.mkdir(parents=True, exist_ok=True)
        (timings / f"{name.replace('/', '--')}.json").write_text(
            json.dumps(timing, indent=2) + "\n"
        )
        print(json.dumps(timing), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
