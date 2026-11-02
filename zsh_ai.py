#!/usr/bin/env python3
"""Entry point for the shell companion. Run `ai help` for controls."""
from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import sys
from pathlib import Path

from store import Store
import terminal


HELP = """ai                       talk here; empty line / Ctrl+D returns to the shell
ai <message>             ask a question here in the shell
ai look                  ask about the latest terminal output
ai task <name>           start or return to a named task
ai tasks                 list saved tasks
ai link %N               share this task with terminal %N
ai unlink %N             put a terminal back in a separate task
ai status                show task and linked terminals
ai remember [global] ... save a fact or preference
ai memory                list task and global notes
ai forget <id>           remove a note
ai pause / ai resume     pause/resume observing this terminal
ai watch errors|all|off   choose when it comments after command output
ai tools                 show available read-only tools
Ctrl+X a                 turn the current prompt into an ai question
Ctrl+X s                 ask about the latest output
Ctrl+X i                 insert a staged command (editable; never auto-runs)
Ctrl+C                   stop an answer
"""


def main(argv: list[str]) -> int:
    verb = argv[0] if argv else "repl"
    args = argv[1:]
    if verb in ("help", "--help", "-h"):
        print(HELP)
        return 0
    if verb == "watch-default":
        from engine import default_watch_mode
        print(default_watch_mode())
        return 0
    source = os.environ.get("TMUX_PANE", "")
    if verb in ("panel", "open", "panel-run") and ("--background" in args or verb == "panel-run"):
        # Compatibility with shells whose old startup hook is still in memory.
        return 0
    if verb == "close":
        for pane in terminal.panes():
            if pane["source"] == source:
                terminal.tmux("kill-pane", "-t", pane["id"])
        return 0
    store = Store()
    store.import_legacy()
    task, pane = terminal.task_for(store, source)
    if verb in ("repl", "panel", "open"):
        from inline import conversation
        return conversation(store, source)
    if verb == "watch-event":
        from inline import watch
        return watch(store, source, args[0]) if args else 1
    if verb == "render-watch":
        from inline import render_watch
        return render_watch(store, source)
    if verb == "watch":
        if args:
            if args not in (["all"], ["errors"], ["off"]):
                raise ValueError("Use: ai watch errors|all|off")
            terminal.set_option(source, "watch", args[0])
        from engine import default_watch_mode
        mode = terminal.tmux("show-options", "-p", "-qv", "-t", source, "@zai_watch") or default_watch_mode()
        print("ai › after-output comments: " + mode)
    elif verb == "tools":
        from read_tools import DEFINITIONS
        for entry in DEFINITIONS:
            tool = entry["function"]
            print(f"{tool['name']}: {tool['description']}")
    elif verb in ("task", "project"):
        if args:
            task = terminal.use_task(store, source, " ".join(args))
        print(task["name"])
    elif verb == "tasks":
        for entry in store.tasks():
            print(("● " if entry["id"] == task["id"] else "  ") + entry["name"])
    elif verb in ("link", "unlink"):
        if len(args) != 1:
            raise ValueError(f"Use: ai {verb} %N")
        getattr(terminal, verb)(store, source, args[0])
        print(f"{args[0]} {'joined this task' if verb == 'link' else 'is now separate'}")
    elif verb == "status":
        print("Task: " + task["name"])
        print("Inline chat · read-only tools · ai watch errors|all|off")
        for p in terminal.panes():
            if not p["source"]:
                print(f"{'●' if p['task'] == task['id'] else '○'} {p['id']}  {p['cwd']}  {p['command']}")
        print("● linked · ○ separate")
    elif verb == "remember":
        scope = "global" if args and args[0] == "global" else task["id"]
        words = args[1:] if scope == "global" else args
        text = " ".join(words).strip()
        if not text or text.casefold() in ("this", "that", "it"):
            raise ValueError("Tell me the fact: ai remember <fact>, or ask in the companion.")
        print("Remembered." if store.remember(scope, terminal.clean(text)) else "Already remembered.")
    elif verb == "memory":
        notes = store.notes(task["id"])
        for note in notes:
            print(f"{note['id']}. [{'global' if note['scope'] == 'global' else task['name']}] {note['text']}")
        if not notes:
            print("No memories for this task yet.")
    elif verb == "forget":
        if len(args) != 1 or not args[0].isdigit():
            raise ValueError("Use: ai forget <id from ai memory>")
        print("Forgotten." if store.forget(int(args[0]), task["id"]) else "No such note in this task.")
    elif verb in ("pause", "resume"):
        terminal.set_option(source, "paused", "1" if verb == "pause" else "0")
        print("Observation " + ("paused." if verb == "pause" else "resumed."))
    elif verb == "take-staged":
        print(store.take(pane["key"], task["id"], pane["event"]), end="")
    elif verb == "context":
        print(json.dumps(terminal.context(store, source)[1], indent=2, ensure_ascii=False))
    elif verb == "greet":
        pass  # Backward compatibility with a hook loaded before the upgrade.
    else:
        from inline import ask
        text = " ".join(args) if verb == "chat" else " ".join(argv)
        if verb == "look":
            text = "Look at the latest command and output. Explain what happened and suggest a next step if needed."
        asyncio.run(ask(store, source, text))
    return 0


if __name__ == "__main__":
    try:
        # A shell that still has the old function loaded invokes system python.
        # Redirect it to the private runtime so `ai` works immediately after upgrade.
        runtime = Path(__file__).resolve().parent / "venv/bin/python"
        if runtime.exists() and Path(sys.prefix) != runtime.parent.parent:
            os.execv(str(runtime), [str(runtime), str(Path(__file__).resolve()), *sys.argv[1:]])
        sys.exit(main(sys.argv[1:]))
    except KeyboardInterrupt:
        sys.exit(130)
    except (RuntimeError, ValueError, OSError, sqlite3.Error) as error:
        print("Companion: " + str(error), file=sys.stderr)
        sys.exit(1)
