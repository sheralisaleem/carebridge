from __future__ import annotations

import asyncio

from ..safety import flag_injection
from .base import Agent, AgentMessage

class PolicyAgent(Agent):
    role = "policy"

    def __init__(self):
        super().__init__("policy", "Policy Librarian", {
            "knowledge": "RAG over clinic documents", "store": "ChromaDB",
        })
        self.intentions = ["answer only from retrieved evidence"]

    async def execute(self, task, context):
        query = task.fields["query"]
        passages = await asyncio.to_thread(context.kb.retrieve, query)
        passages = [passage for passage in passages if not flag_injection(passage.text)]
        context.emit("rag", query=query, passages=[
            {"source": passage.source, "section": passage.section, "score": passage.score}
            for passage in passages
        ])
        return {"ok": True, **context.kb.as_tool_result(passages)}

