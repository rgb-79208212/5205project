"""NDJSON compatibility entrypoint for the existing workspace layout."""

from core.agent import TOOLS as TOOLS
from core.agent import Research
from core.task_graph import ResearchGraph
from research import setting


async def investigate(question, session, model_json, emit, directory=None):
    if not isinstance(question, str) or not question.strip():
        raise ValueError("A research question is required.")
    if hasattr(session, "fork") and setting("RESEARCH_ENGINE", "graph") == "graph":
        return await ResearchGraph(
            question.strip(), session, model_json, emit, directory
        ).run()
    return await Research(question.strip(), session, model_json, emit, directory).run()
