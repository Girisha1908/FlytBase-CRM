"""
mcp_client.py - a tiny client for the FlytBase CRM MCP server.

MCP ("Model Context Protocol") over HTTP is just JSON-RPC 2.0 sent with POST:
  1. "initialize"                -> server replies and may hand us a session id
  2. "notifications/initialized" -> we tell the server we're ready (no reply)
  3. "tools/call"                -> run a tool such as whoami or get_my_accounts

Both connect.py and (later) the Flask app import call_tool() from here, so the
protocol details live in one place.

The token is read from the AQ_TOKEN environment variable (loaded from .env).
It is only ever sent to the MCP server, never printed and never sent to a browser.
"""

import json
import os
import re
import time

import requests
from dotenv import load_dotenv

MCP_URL = "https://questathon.aihackerscollective.com/api/public/mcp"

# Load variables from .env into os.environ (does nothing if they're already set).
load_dotenv()


class RateLimited(Exception):
    """Raised when the server says we've used our one call for this minute."""


def _is_rate_limit(text):
    """
    The server reports the limit in-band, e.g.
    {"error": "Too many requests: one call a minute. Try again in 38 seconds.",
     "retry_after_seconds": 38}
    """
    t = text.lower()
    return any(k in t for k in ("too many requests", "retry_after", "rate limit", "one call a minute"))


def _token():
    token = os.environ.get("AQ_TOKEN")
    if not token:
        raise RuntimeError("AQ_TOKEN is not set. Set it as an environment variable "
                           "(on Render: Environment), or in .env when running locally.")
    return token


def _parse_response(resp):
    """
    The server may answer with plain JSON or with a Server-Sent Events stream
    ("data: {...}" lines). Handle both and return the JSON-RPC message.
    """
    if resp.status_code == 429:
        raise RateLimited("MCP server returned 429 (one call per minute per token).")
    resp.raise_for_status()

    content_type = resp.headers.get("Content-Type", "")
    if "text/event-stream" in content_type:
        # Take the last "data:" line that holds a JSON-RPC response.
        message = None
        for line in resp.text.splitlines():
            if line.startswith("data:"):
                payload = line[len("data:"):].strip()
                if payload:
                    message = json.loads(payload)
        if message is None:
            raise RuntimeError("Empty event stream from MCP server.")
        return message
    return resp.json()


def call_tool(name, arguments=None):
    """
    Open a session, call one MCP tool, and return its result as Python data.

    Note: each call to this function is one tool call against your rate limit.
    """
    return _with_session(lambda rpc: _call(rpc, name, arguments or {}))


# Our names for common arguments, and the names a server might use for them.
ARG_ALIASES = {
    "account": ["account", "account_id", "accountId", "id"],
    "kind": ["kind", "type", "category"],
    "text": ["text", "body", "content", "note", "message"],
}


def call_mapped(name, **values):
    """
    Call a tool whose argument names we don't hard-code. Inside the same
    session we ask the server for the tool's input schema (tools/list is a
    protocol request, not a tool call) and map our values onto it, e.g.
    call_mapped("get_all_paperwork", account="K").
    Returns (result, arguments_sent).
    """
    def run(rpc):
        tools = rpc("tools/list", {}).get("result", {}).get("tools", [])
        schema = next((t.get("inputSchema", {}) for t in tools if t.get("name") == name), {})
        props = list(schema.get("properties", {}))
        args = {}
        for ours, value in values.items():
            candidates = ARG_ALIASES.get(ours, [ours])
            args[next((c for c in candidates if c in props), ours)] = value
        return _call(rpc, name, args), args

    return _with_session(run)


def write_note(account, kind, text):
    """Call write_note. Returns (result, arguments_sent)."""
    return call_mapped("write_note", account=account, kind=kind, text=text)


def call_patiently(name, tries=6, **values):
    """
    For one-off scripts (not the app's worker): call a tool, and if the
    one-call-a-minute limit is hit, wait exactly as long as the server says
    ("retry_after_seconds") and try again.
    """
    for attempt in range(1, tries + 1):
        try:
            if values:
                return call_mapped(name, **values)[0]
            return call_tool(name)
        except RateLimited as e:
            m = re.search(r'"retry_after_seconds":\s*(\d+)', str(e))
            wait = int(m.group(1)) + 0.3 if m else 61
            print(f"  [{name}] one call a minute: retrying in {wait:.0f}s", flush=True)
            time.sleep(wait)
    raise RateLimited(f"{name}: still rate limited after {tries} tries")


def _call(rpc, name, arguments):
    """Send tools/call over an open session and unwrap the result."""
    message = rpc("tools/call", {"name": name, "arguments": arguments})
    if "error" in message:
        err = message["error"]
        text = str(err.get("message", err))
        if _is_rate_limit(text):
            raise RateLimited(text)
        raise RuntimeError(f"MCP error calling {name}: {text}")
    return _extract_result(name, message["result"])


def _with_session(work):
    """
    Open an MCP session (initialize + initialized notification), then hand an
    rpc(method, params) function to `work` and return whatever it returns.
    """
    headers = {
        "Authorization": f"Bearer {_token()}",
        "Content-Type": "application/json",
        # Streamable-HTTP MCP servers require us to accept both formats.
        "Accept": "application/json, text/event-stream",
    }

    with requests.Session() as http:
        # --- Step 1: initialize the MCP session -------------------------------
        init = http.post(MCP_URL, headers=headers, timeout=30, json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "flytbase-crm", "version": "0.1"},
            },
        })
        _parse_response(init)

        # The server may give us a session id; later requests must echo it.
        session_id = init.headers.get("Mcp-Session-Id")
        if session_id:
            headers["Mcp-Session-Id"] = session_id

        # --- Step 2: tell the server we're initialized (a notification) ------
        http.post(MCP_URL, headers=headers, timeout=30, json={
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
        })

        # --- Step 3: let the caller send its request(s) -----------------------
        next_id = [2]

        def rpc(method, params):
            resp = http.post(MCP_URL, headers=headers, timeout=60, json={
                "jsonrpc": "2.0", "id": next_id[0], "method": method, "params": params,
            })
            next_id[0] += 1
            return _parse_response(resp)

        return work(rpc)


def _extract_result(name, result):
    """
    MCP tool results come back as {"content": [{"type": "text", "text": ...}]},
    sometimes with a ready-made "structuredContent". Turn it into plain data.
    """
    if result.get("isError"):
        text = " ".join(c.get("text", "") for c in result.get("content", []))
        if _is_rate_limit(text):
            raise RateLimited(text)
        raise RuntimeError(f"Tool {name} failed: {text}")

    if "structuredContent" in result:
        return result["structuredContent"]

    texts = [c["text"] for c in result.get("content", []) if c.get("type") == "text"]
    joined = "\n".join(texts)
    try:
        return json.loads(joined)   # most tools return JSON as text
    except json.JSONDecodeError:
        return joined               # otherwise keep the raw text
