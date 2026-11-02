"""Explicit tmux task membership. Directory equality never implies shared work."""
from __future__ import annotations

import re
import subprocess
import uuid

from store import Store


def tmux(*args: str, check: bool = True) -> str:
    result = subprocess.run(["tmux", *args], capture_output=True, text=True, timeout=5)
    if check and result.returncode:
        raise RuntimeError(result.stderr.strip() or "tmux is unavailable")
    return result.stdout.rstrip("\n") if result.returncode == 0 else ""


FIELDS = ("pane_id", "pane_pid", "session_name", "window_id", "pane_current_path",
          "pane_current_command", "@zai_task", "@zai_source", "@zai_ready",
          "@zai_command", "@zai_exit", "@zai_event", "@zai_paused", "socket_path",
          "pid", "@zai_protocol")
KEYS = ("id", "pid", "session", "window", "cwd", "command", "task", "source",
        "ready", "last_command", "exit", "event", "paused", "socket", "server_pid", "protocol")


def panes() -> list[dict]:
    raw = tmux("list-panes", "-a", "-F", "\t".join("#{" + f + "}" for f in FIELDS), check=False)
    result = []
    for line in raw.splitlines():
        values = line.split("\t")
        if len(values) != len(KEYS):
            continue
        pane = dict(zip(KEYS, values))
        pane["key"] = f"{pane['socket']}:{pane['server_pid']}:{pane['id']}:{pane['pid']}"
        result.append(pane)
    return result


def pane_by_id(pane_id: str) -> dict:
    if not re.fullmatch(r"%\d+", pane_id or ""):
        raise RuntimeError("Open a tmux terminal first; no source terminal was specified.")
    pane = next((p for p in panes() if p["id"] == pane_id and not p["source"]), None)
    if not pane:
        raise RuntimeError(f"Terminal {pane_id} is closed or is an assistant pane.")
    return pane


def set_option(pane: str, key: str, value: str):
    tmux("set-option", "-p", "-t", pane, "@zai_" + key, value)


def task_for(store: Store, pane_id: str) -> tuple[dict, dict]:
    key = pane_by_id(pane_id)["key"]
    with store.lock("task:" + key):
        pane = pane_by_id(pane_id)
        task = store.get_task(pane["task"])
        if not task:
            # UUID suffix also separates shells across server restarts and multiple servers.
            task = store.task(f"Terminal {pane_id} · {uuid.uuid4().hex[:5]}")
            set_option(pane_id, "task", task["id"])
            pane["task"] = task["id"]
        return task, pane


def use_task(store: Store, pane_id: str, name: str) -> dict:
    pane_by_id(pane_id)
    task = store.task(name)
    set_option(pane_id, "task", task["id"])
    return task


def link(store: Store, source: str, peer: str):
    task, _ = task_for(store, source)
    pane_by_id(peer)
    set_option(peer, "task", task["id"])


def unlink(store: Store, source: str, peer: str):
    task, _ = task_for(store, source)
    pane = pane_by_id(peer)
    if pane["task"] != task["id"]:
        raise ValueError("That terminal is not linked to this task.")
    set_option(peer, "task", "")
    task_for(store, peer)


ANSI = re.compile(r"\x1b\][^\x07]*(?:\x07|\x1b\\)|\x1b\[[0-?]*[ -/]*[@-~]")
SECRETS = re.compile(
    r"(?i)((?:authorization\s*:\s*bearer\s+)|(?:\b(?:api[_-]?key|access[_-]?token|"
    r"password|secret|token)\s*[=:]\s*[\"']?))([^\s\"']+)"
)


def clean(text: str) -> str:
    text = ANSI.sub("", text)
    text = "".join(c for c in text if c in "\n\t" or (ord(c) >= 32 and ord(c) != 127))
    text = SECRETS.sub(r"\1[redacted]", text)
    return re.sub(r"\bsk-[A-Za-z0-9_-]{20,}\b", "[redacted]", text)


def capture(pane: str, lines: int = 60) -> str:
    info = pane_by_id(pane)
    if info["ready"] == "1" and info["protocol"] == "3":
        # A fresh shell has no completed-command snapshot yet. Do not confuse
        # that with a saved, legitimately empty result (for example `true`).
        saved = tmux("show-options", "-p", "-q", "-t", pane, "@zai_output", check=False)
        if saved:
            raw = tmux("show-options", "-p", "-qv", "-t", pane, "@zai_output", check=False)
            return clean(raw)[-8000:]
    raw = tmux("capture-pane", "-p", "-J", "-t", pane, "-S", f"-{lines}")
    return "\n".join(line.rstrip() for line in clean(raw).splitlines()
                     if not line.lstrip().startswith("│"))[-8000:]


def context(store: Store, source: str, budget: int = 14000) -> tuple[dict, dict]:
    task, current = task_for(store, source)
    members = [p for p in panes() if not p["source"] and p["task"] == task["id"]]
    members.sort(key=lambda p: p["id"] != source)
    blocks = []
    remaining = budget
    # Related panes share a total budget; unrelated panes' content is never captured.
    for index, pane in enumerate(members[:6]):
        allowance = min(8000 if index == 0 else 3000, max(0, remaining))
        screen = "[Observation paused]" if pane["paused"] == "1" else capture(pane["id"])[-allowance:] if allowance else "[Context budget reached]"
        remaining -= len(screen)
        blocks.append({
            "terminal": pane["id"], "current": pane["id"] == source,
            "event": pane["event"],
            "observation_paused": pane["paused"] == "1",
            "cwd": clean(pane["cwd"]), "running": clean(pane["command"]),
            "last_command": "" if pane["paused"] == "1" else clean(pane["last_command"])[:1000],
            "last_exit": pane["exit"] if pane["ready"] == "1" else None,
            "at_prompt": pane["ready"] == "1", "screen": screen,
        })
    return task, {"task": task["name"], "current_terminal": source, "source_key": current["key"], "terminals": blocks,
                  "other_terminals": sum(not p["source"] and p["task"] != task["id"] for p in panes()),
                  "omitted_linked_terminals": max(0, len(members) - 6)}
