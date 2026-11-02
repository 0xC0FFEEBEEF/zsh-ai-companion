"""Plain scrollback interaction and quiet asynchronous comments after shell output."""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import textwrap
import time

from engine import Companion
from store import Store
import terminal


def say(text: str, label="ai"):
    width = max(30, min(110, shutil.get_terminal_size().columns - 2))
    color = sys.stdout.isatty() and not os.environ.get("NO_COLOR")
    prefix = f"\x1b[36m{label} ›\x1b[0m " if color else label + " › "
    first = True
    for paragraph in terminal.clean(text).splitlines():
        lines = textwrap.wrap(paragraph, width=width - 5, replace_whitespace=False, break_long_words=False) or [""]
        for line in lines:
            print((prefix if first else "     ") + line)
            first = False


def render_result(store: Store, source: str, result: dict):
    if not result["reply"].strip():
        return
    say(result["reply"])
    if result.get("command"):
        task, pane = terminal.task_for(store, source)
        if (task["id"] == result["task_id"] and pane["key"] == result["pane_key"]
                and pane["event"] == result["event"]):
            store.stage(pane["key"], task["id"], result["command"], pane["event"])
            print("     $ " + result["command"])
            color = sys.stdout.isatty() and not os.environ.get("NO_COLOR")
            hint = "     Ctrl+X i to bring that into your prompt"
            print("\x1b[2m" + hint + "\x1b[0m" if color else hint)
    if result.get("saved"):
        print("     remembered: " + result["saved"])


async def ask(store: Store, source: str, text: str, companion=None):
    companion = companion or Companion(store)
    started = time.monotonic()
    current = "thinking"
    tty = sys.stderr.isatty()
    def progress(phase):
        nonlocal current
        current = phase.lower()
    async def status():
        while True:
            if tty:
                width = max(20, shutil.get_terminal_size().columns - 2)
                sys.stderr.write("\r\x1b[2K" + f"ai · {current}… {int(time.monotonic() - started)}s"[:width])
                sys.stderr.flush()
            await asyncio.sleep(.2)
    ticker = asyncio.create_task(status())
    try:
        result = await companion.ask(source, text, progress)
    finally:
        ticker.cancel()
        await asyncio.gather(ticker, return_exceptions=True)
        if tty:
            sys.stderr.write("\r\x1b[2K")
            sys.stderr.flush()
    render_result(store, source, result)


def conversation(store: Store, source: str):
    import readline
    say("I'm here. An empty line takes you back to the shell.")
    companion = Companion(store)
    while True:
        try:
            text = input("you › ").strip()
            if not text or text in ("/exit", "/quit"):
                return 0
            asyncio.run(ask(store, source, text, companion))
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        except (RuntimeError, ValueError, OSError) as exc:
            say(str(exc))


def watch(store: Store, source: str, event: str):
    """Writes one JSON line to zsh's private pipe, not into the terminal."""
    # Give rapid command sequences time to settle before starting a model turn.
    # The shell cancels this worker as soon as the next command begins.
    time.sleep(0.6)
    task, pane = terminal.task_for(store, source)
    if pane["event"] != event or pane["ready"] != "1" or pane["paused"] == "1":
        return 0
    text = f"Automatic observation: command {pane['last_command']!r} finished with exit status {pane['exit']}."
    try:
        result = asyncio.run(Companion(store).ask(source, text, automatic=True))
        current_task, current_pane = terminal.task_for(store, source)
        if (result["reply"].strip() and current_task["id"] == task["id"] and current_pane["key"] == pane["key"]
                and current_pane["event"] == event and current_pane["paused"] != "1"):
            print(json.dumps({**result, "trigger": text}, ensure_ascii=False), flush=True)
    except (RuntimeError, ValueError, OSError) as exc:
        if os.environ.get("ZSH_AI_DEBUG"):
            print("watch: " + str(exc), file=sys.stderr)
    return 0


def render_watch(store: Store, source: str):
    try:
        result = json.loads(sys.stdin.readline(24000))
        task, pane = terminal.task_for(store, source)
        if (result["task_id"] != task["id"] or result["pane_key"] != pane["key"]
                or result["event"] != pane["event"] or pane["ready"] != "1" or pane["paused"] == "1"):
            if os.environ.get("ZSH_AI_DEBUG"):
                print("watch reply expired: " + repr((result.get("event"), pane["event"], result.get("pane_key"), pane["key"])), file=sys.stderr)
            return 0
        if result["reply"].strip():
            store.exchange(task["id"], source, result["trigger"], result["reply"])
            render_result(store, source, result)
    except (ValueError, KeyError, OSError, RuntimeError) as exc:
        if os.environ.get("ZSH_AI_DEBUG"):
            print("render-watch: " + str(exc), file=sys.stderr)
    return 0
