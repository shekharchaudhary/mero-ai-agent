"""Minimal MCP server skeleton — one resource and one tool, following the
patterns in SKILL.md.

This server exposes:
  - Resource:  notes://list                — read-only directory of note titles
  - Tool:      create_note(title, body)    — reversible write, returns the new note id

Install:
    pip install mcp

Run (stdio transport):
    python mcp_server_skeleton.py
"""

from __future__ import annotations

import re
import uuid
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Resource, TextContent, Tool

# In-memory store; swap for a real backend in prod.
_NOTES: dict[str, dict[str, str]] = {}

server = Server("notes")

TITLE_RE = re.compile(r"^[\w\-. ]{1,80}$")


@server.list_resources()
async def list_resources() -> list[Resource]:
    return [
        Resource(
            uri="notes://list",
            name="Note titles",
            description="A directory listing of all notes by id and title. Read-only.",
            mimeType="application/json",
        )
    ]


@server.read_resource()
async def read_resource(uri: str) -> str:
    if uri != "notes://list":
        raise ValueError(f"Unknown resource: {uri}")
    payload = [{"id": nid, "title": n["title"]} for nid, n in _NOTES.items()]
    return str(payload)


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="create_note",
            description=(
                "Create a new note with a title and body. "
                "Use update_note to modify an existing note — this tool only creates."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "1-80 chars, word chars / dashes / dots / spaces only.",
                    },
                    "body": {
                        "type": "string",
                        "description": "Plain-text body. Max 10,000 chars.",
                    },
                    "idempotency_key": {
                        "type": "string",
                        "description": (
                            "Optional. Retrying with the same key returns the existing note "
                            "rather than creating a duplicate."
                        ),
                    },
                },
                "required": ["title", "body"],
            },
        )
    ]


def _envelope(ok: bool, summary: str, **rest: Any) -> dict[str, Any]:
    return {"ok": ok, "summary": summary, **rest}


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    if name != "create_note":
        return [TextContent(
            type="text",
            text=str(_envelope(False, f"Unknown tool: {name}", error={"code": "not_found"})),
        )]

    title = arguments.get("title", "")
    body = arguments.get("body", "")
    idem = arguments.get("idempotency_key")

    if not TITLE_RE.match(title):
        return [TextContent(
            type="text",
            text=str(_envelope(
                False,
                "title must be 1-80 chars (word chars, dashes, dots, spaces).",
                error={"code": "invalid_argument", "retryable": False},
            )),
        )]
    if not body.strip() or len(body) > 10_000:
        return [TextContent(
            type="text",
            text=str(_envelope(
                False,
                "body must be non-empty and <=10,000 chars.",
                error={"code": "invalid_argument", "retryable": False},
            )),
        )]

    if idem:
        for nid, n in _NOTES.items():
            if n.get("idempotency_key") == idem:
                return [TextContent(
                    type="text",
                    text=str(_envelope(True, "Returning existing note (idempotency hit).", data={"id": nid})),
                )]

    nid = uuid.uuid4().hex[:12]
    _NOTES[nid] = {"title": title, "body": body, "idempotency_key": idem or ""}
    return [TextContent(
        type="text",
        text=str(_envelope(True, f"Created note {nid}.", data={"id": nid, "title": title})),
    )]


async def main() -> None:
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
