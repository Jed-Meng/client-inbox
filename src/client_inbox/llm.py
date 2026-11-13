"""The user's own Claude, used strictly as text in, text out.

Calls the Claude Code CLI in print mode. It uses whatever the user is logged in with
(Claude subscription, or ANTHROPIC_API_KEY if that is how their CLI is set up).

Incoming email is untrusted text, so every call runs with:
  --tools ""            no tools at all: Claude cannot read files, run commands or browse
  --strict-mcp-config   no MCP servers or connectors
  --safe-mode           no user CLAUDE.md, hooks, skills or plugins leaking into the prompt
  an empty working directory, and no saved session
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess

from . import db

MODELS = {
    "fast": os.environ.get("CLIENT_INBOX_FAST_MODEL", "haiku"),
    "smart": os.environ.get("CLIENT_INBOX_SMART_MODEL", "sonnet"),
}


class LLMError(RuntimeError):
    pass


def available() -> bool:
    return shutil.which("claude") is not None


def run(prompt: str, *, system: str, tier: str = "fast", schema: dict | None = None, timeout: int = 240,
        attachments: list[tuple[str, bytes]] | None = None):
    """Return Claude's reply: a dict when `schema` is given, otherwise a string.

    `attachments` are (media type, bytes) pairs: images and PDFs Claude should look at.
    They are sent inside the message itself, so tools stay off."""
    binary = shutil.which("claude")
    if not binary:
        raise LLMError("Claude Code is not installed. Install it and run `claude` once to log in.")
    workdir = db.home() / "llm_cwd"
    workdir.mkdir(exist_ok=True)
    command = [
        binary, "-p",
        "--safe-mode",
        "--tools", "",
        "--strict-mcp-config",
        "--no-session-persistence",
        "--model", MODELS[tier],
        "--system-prompt", system,
    ]
    if schema:
        command += ["--json-schema", json.dumps(schema)]
    if attachments:
        content = []
        for media_type, data in attachments:
            kind = "document" if media_type == "application/pdf" else "image"
            content.append({"type": kind, "source": {
                "type": "base64", "media_type": media_type, "data": base64.b64encode(data).decode("ascii"),
            }})
        content.append({"type": "text", "text": prompt})
        stdin = json.dumps({"type": "user", "message": {"role": "user", "content": content}}) + "\n"
        command += ["--input-format", "stream-json", "--output-format", "stream-json", "--verbose"]
    else:
        stdin = prompt
        command += ["--output-format", "json"]
    try:
        proc = subprocess.run(
            command, input=stdin, capture_output=True, text=True, timeout=timeout, cwd=workdir
        )
    except subprocess.TimeoutExpired as exc:
        raise LLMError(f"Claude did not answer within {timeout} seconds.") from exc
    payload = _result(proc.stdout)
    if payload is None:
        detail = (proc.stderr or proc.stdout or "").strip()[:400]
        raise LLMError(f"Claude returned no usable output. {detail}")
    if payload.get("is_error"):
        raise LLMError(str(payload.get("result") or payload.get("subtype") or "Claude reported an error")[:400])
    if schema:
        structured = payload.get("structured_output")
        if not isinstance(structured, dict):
            try:
                structured = json.loads(payload.get("result") or "")
            except json.JSONDecodeError as exc:
                raise LLMError("Claude did not return the expected structured reply.") from exc
        return structured
    return (payload.get("result") or "").strip()


def _result(stdout: str) -> dict | None:
    """The final result object, from either single-JSON or line-per-event output."""
    try:
        whole = json.loads(stdout)
        if isinstance(whole, dict):
            return whole
    except json.JSONDecodeError:
        pass
    for line in reversed(stdout.splitlines()):
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict) and event.get("type") == "result":
            return event
    return None
