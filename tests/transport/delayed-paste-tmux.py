"""Isolated tmux/CLI model: enqueue succeeds before the CLI processes paste.

Enter during the paste-processing window leaves text in the editor, even though
tmux exits zero. An unrelated producer changes the top buffer before each paste.
Never invokes real tmux. Both language senders use this same regression fixture.
"""
import json
import os
import sys
import time
from pathlib import Path

root = Path(os.environ["TMUX_PASTE_STATE"])
root.mkdir(parents=True, exist_ok=True)
args = sys.argv[1:]
operation = args[0]
with (root / "calls.jsonl").open("a") as handle:
    handle.write(json.dumps(args) + "\n")
path = root / "state.json"
state = json.loads(path.read_text()) if path.exists() else {"buffers": {}}


def option(name, default=None):
    return args[args.index(name) + 1] if name in args else default


if operation == "load-buffer":
    state["buffers"][option("-b", "ambient")] = Path(args[-1]).read_text()
elif operation == "paste-buffer":
    state["buffers"]["ambient"] = "foreign clipboard"
    buffer = option("-b", "ambient")
    state["pasted"] = state["buffers"][buffer]
    state["ready_at"] = time.monotonic() + 0.3
    (root / "pasted.txt").write_text(state["pasted"])
    if "-d" in args:
        del state["buffers"][buffer]
elif operation == "send-keys":
    if time.monotonic() >= state.get("ready_at", float("inf")):
        (root / "submitted.txt").write_text(state["pasted"])
elif operation == "delete-buffer":
    state["buffers"].pop(option("-b"), None)
elif operation == "capture-pane":
    # A misleading old marker is deliberately not proof of acceptance.
    print("Working (old pane diagnostic)")
elif operation == "display-message":
    print("isolated")
else:
    raise SystemExit("unsupported isolated tmux operation: " + operation)
path.write_text(json.dumps(state))
if os.environ.get("TMUX_PASTE_FAIL") == operation:
    raise SystemExit("simulated lost tmux response after " + operation)
