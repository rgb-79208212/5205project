"""Run research or explicitly resume an incomplete graph, preserving prior attempts."""

import argparse
import asyncio
import json
import sys
from uuid import uuid4

from core.task_graph import ResearchGraph
from research import ROOT, connect, model_json


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("question", nargs="?")
    parser.add_argument("--resume", metavar="RUN_ID")
    parser.add_argument("--parallelism", type=int, choices=[1, 2])
    args = parser.parse_args()
    directory = (ROOT / "runs" / (args.resume or uuid4().hex)).resolve()
    if not directory.is_relative_to((ROOT / "runs").resolve()):
        parser.error("Run ID must stay inside this project's runs directory")
    if args.resume:
        previous = json.loads(
            (directory / "research-state.json").read_text(encoding="utf-8")
        )
        question = previous["question"]
        if args.question and args.question != question:
            parser.error("Resume cannot change the original question")
    else:
        question = args.question
        if not question or not question.strip():
            parser.error("Provide a research question")

    def emit(kind, data):
        print(json.dumps({"event": kind, **data}, ensure_ascii=False), flush=True)

    async with connect(directory) as host:
        result = await ResearchGraph(
            question, host, model_json, emit, directory, args.parallelism
        ).run(resume=bool(args.resume))
    print(
        json.dumps(
            {
                "run_id": directory.name,
                "status": result["status"],
                "directory": str(directory),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(main())
