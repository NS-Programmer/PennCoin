"""Send traces to PRISM by Block Convey so every AI call is recorded and scored.

Uses PRISM's REST trace API (POST {host}/api/traces, key in the X-PRISMtrace-Key header).
PRISM's proxy only forwards to Anthropic, OpenAI and Gemini, so for Nicrron we trace by REST.

Off unless PRISMTRACE_API_KEY and PRISMTRACE_PROJECT_ID are set (.env or environment).
Sending happens on a background thread and never raises: PRISM being down never blocks a draft.
"""
import json
import os
import threading
import uuid

DEFAULT_HOST = "https://api.prism.blockconvey.com"
_last_error = None


def enabled() -> bool:
    return bool(os.environ.get("PRISMTRACE_API_KEY") and os.environ.get("PRISMTRACE_PROJECT_ID"))


def build_trace(model, input_messages, output_text, latency_ms, *, agent_id, agent_name=None,
                session_id=None, tokens_in=None, tokens_out=None, metadata=None, trace_id=None) -> dict:
    body = {
        "project_id": os.environ.get("PRISMTRACE_PROJECT_ID", ""),
        "trace_id": trace_id or str(uuid.uuid4()),
        "model": model,
        "input_messages": [{"role": m["role"], "content": m["content"]} for m in input_messages],
        "output_message": {"role": "assistant", "content": output_text},
        "latency_ms": int(latency_ms or 0),
        "agent_id": agent_id,
        "agent_name": agent_name or agent_id,
        "metadata": {"app": "penncoin-filings-desk", "simulated_data": True, **(metadata or {})},
    }
    if session_id:
        body["session_id"] = session_id
    if tokens_in is not None:
        body["token_count_input"] = int(tokens_in)
    if tokens_out is not None:
        body["token_count_output"] = int(tokens_out)
    return body


def _post(body: dict):
    global _last_error
    import urllib.request
    host = os.environ.get("PRISMTRACE_HOST", DEFAULT_HOST).rstrip("/")
    req = urllib.request.Request(
        f"{host}/api/traces", data=json.dumps(body).encode(), method="POST",
        headers={"X-PRISMtrace-Key": os.environ["PRISMTRACE_API_KEY"], "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            r.read()
        _last_error = None
    except Exception as err:  # never let tracing break the app
        _last_error = f"{type(err).__name__}: {err}"


def send_trace(*args, wait=False, **kwargs):
    """Build and send one trace. Returns the trace body (or None when PRISM is off)."""
    if not enabled():
        return None
    body = build_trace(*args, **kwargs)
    if wait:
        _post(body)
    else:
        threading.Thread(target=_post, args=(body,), daemon=True).start()
    return body


def last_error():
    return _last_error
