"""A small read-only toolset. No general shell, plugins, pager, or write operations."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import platform
import re
import shlex
import shutil
import signal
import stat
import time

import terminal
from store import Store


def spec(name, description, properties=None, required=()):
    return {"type": "function", "function": {"name": name, "description": description,
            "parameters": {"type": "object", "properties": properties or {},
                           "required": list(required), "additionalProperties": False}}}


STRING = {"type": "string"}
DEFINITIONS = [
    spec("list_directory", "List files in a local task directory. Defaults to the current terminal's cwd.", {"path": STRING}),
    spec("read_file", "Read a local text file with line numbers. Task directories and non-secret /etc files only.",
         {"path": STRING, "start_line": {"type": "integer"}, "lines": {"type": "integer"}}, ("path",)),
    spec("search_files", "Find a literal text string in local task files. Skips secrets, hidden directories, binaries and symlinks.",
         {"query": STRING, "path": STRING}, ("query",)),
    spec("git_inspect", "Read Git status, diff, or recent commits. External diff, textconv, hooks and fsmonitor are disabled.",
         {"view": {"type": "string", "enum": ["status", "diff", "log"]}, "path": STRING}, ("view",)),
    spec("read_system_info", "Read this local machine's hostname, OS, memory, CPU count, load and disk space."),
    spec("list_processes", "Read local process names, PIDs, owners, terminals and usage. Optionally filter by exact process name, e.g. pacman. Omits command arguments.",
         {"name": STRING}),
    spec("inspect_process", "Read a local PID's owner, terminal, state, age and parent chain to find what launched it. Omits arguments and environment.",
         {"pid": {"type": "integer"}}, ("pid",)),
    spec("service_status", "Read local systemd unit state and recent logs; does not start, stop or reload anything.",
         {"unit": STRING, "user": {"type": "boolean"}}, ("unit",)),
    spec("read_terminal", "Read recent output from the current terminal or one explicitly linked to this task.",
         {"pane": STRING}, ("pane",)),
]

DENIED_PARTS = {".ssh", ".gnupg", ".aws", ".azure", ".kube", ".docker", ".git", ".password-store",
                ".netrc", ".npmrc", ".pypirc", ".git-credentials",
                "keyrings", "google-chrome", "chromium", "firefox", "BraveSoftware", "sops", "gcloud"}
DENIED_NAME = re.compile(r"(?i)(^\.env(?:\.|$)|(?:^|[_.-])(?:api.?key|password|passwd|shadow|secret|token|credentials|"
                         r"credential|cookie|auth)(?:[_.-]|$)|^id_(rsa|ed25519|ecdsa)|\.(?:pem|key|p12|pfx|kdbx)$|"
                         r"(?:^|[_.-])(?:histfile|history)(?:[_.-]|$))")


def process_check(text: str, command: str) -> dict | None:
    """Resolve selected process diagnostics before asking the model to interpret them.

    Only fixed read-only tools are chosen here; shell text is never executed.
    A numeric parent PID alone is not evidence of its program or launching user.
    """
    try:
        words = shlex.split(command)
    except ValueError:
        words = []
    if words and Path(words[0]).name in ("ssh", "mosh"):
        return None  # Local process IDs cannot explain a remote process.
    match = re.search(r"\b(?:pid|process)\s*(?:pid\s*)?[#:=]?\s*(\d+)\b", text, re.I)
    if match:
        return {"name": "inspect_process", "arguments": {"pid": int(match[1])}}
    if not words or words[0] not in ("ps", "/usr/bin/ps", "/bin/ps"):
        return None
    for index, word in enumerate(words[1:], 1):
        value = words[index + 1] if index + 1 < len(words) else ""
        if word.startswith("-p") and word != "-p":
            value = word[2:]
            word = "-p"
        if word in ("-p", "--pid") and value.isdigit():
            return {"name": "inspect_process", "arguments": {"pid": int(value)}}
        if word == "-C" and re.fullmatch(r"[A-Za-z0-9_.+-]{1,80}", value):
            return {"name": "list_processes", "arguments": {"name": value}}
    return None


class ReadTools:
    def __init__(self, store: Store, source: str, task_id: str):
        self.store, self.source, self.task_id = store, source, task_id
        self.cwd = Path(terminal.pane_by_id(source)["cwd"]).resolve()

    def roots(self) -> list[Path]:
        return [Path(p["cwd"]).resolve() for p in terminal.panes()
                if not p["source"] and p["task"] == self.task_id and p["paused"] != "1"]

    def resolve_path(self, value: str, etc: bool = False) -> Path:
        if not isinstance(value, str) or len(value) > 4096 or "\x00" in value:
            raise ValueError("Invalid path")
        candidate = Path(value).expanduser()
        path = (candidate if candidate.is_absolute() else self.cwd / candidate).resolve(strict=True)
        roots = self.roots() + ([Path("/etc")] if etc else [])
        if not any(path.is_relative_to(root) for root in roots):
            raise ValueError("That path is outside the linked terminals' working directories" + (" and /etc" if etc else ""))
        if any(part in DENIED_PARTS or DENIED_NAME.search(part) for part in path.parts):
            raise ValueError("Credential and private-history files are excluded")
        private_state = self.store.path.parent.resolve()
        private_config = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "zsh-ai"
        if path.is_relative_to(private_state) or path.is_relative_to(private_config.resolve()):
            raise ValueError("Assistant credentials and internal state are excluded")
        if any(path.is_relative_to(Path(p)) for p in ("/dev", "/proc", "/sys", "/run")):
            raise ValueError("Special files are excluded; use read_system_info or service_status")
        return path

    def read_text(self, path: Path, limit: int = 256000) -> str:
        # Re-resolve and verify the opened descriptor, including symlink swaps.
        path = self.resolve_path(str(path), etc=True)
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise ValueError("Only regular text files can be read")
            actual = Path(os.readlink(f"/proc/self/fd/{fd}"))
            self.resolve_path(str(actual), etc=True)
            data = os.read(fd, limit + 1)
            if b"\x00" in data:
                raise ValueError("Binary files are excluded")
            text = data[:limit].decode("utf-8", errors="replace")
            return text + ("\n[File truncated]" if len(data) > limit else "")
        finally:
            os.close(fd)

    async def run(self, name: str, args: dict) -> dict:
        try:
            task, pane = terminal.task_for(self.store, self.source)
            if task["id"] != self.task_id or pane["paused"] == "1":
                raise ValueError("Observation is paused or the terminal changed tasks")
            definition = next((d["function"] for d in DEFINITIONS if d["function"]["name"] == name), None)
            if not definition or not isinstance(args, dict):
                raise ValueError("Unknown read-only tool")
            schema = definition["parameters"]
            if set(args) - set(schema["properties"]) or set(schema["required"]) - set(args):
                raise ValueError("Invalid tool arguments")
            for key, value in args.items():
                expected = {"string": str, "integer": int, "boolean": bool}[schema["properties"][key]["type"]]
                if type(value) is not expected:
                    raise ValueError(f"Invalid {key} type")
            if name in ("git_inspect", "list_processes", "inspect_process", "service_status"):
                text = await getattr(self, name)(**args)
            else:
                text = await asyncio.to_thread(getattr(self, name), **args)
            return {"ok": True, "host": platform.node(), "output": terminal.clean(str(text))[:9000]}
        except (ValueError, OSError, RuntimeError) as exc:
            return {"ok": False, "error": terminal.clean(str(exc))[:600]}

    def list_directory(self, path=".") -> str:
        directory = self.resolve_path(path)
        if not directory.is_dir():
            raise ValueError("Not a directory")
        rows = []
        # Scan a bounded number of entries, without following symlinks.
        with os.scandir(directory) as entries:
            for index, entry in enumerate(entries):
                if index >= 250:
                    rows.append("[More entries omitted]")
                    break
                if entry.name in DENIED_PARTS or DENIED_NAME.search(entry.name):
                    continue
                kind = "link" if entry.is_symlink() else "dir" if entry.is_dir(follow_symlinks=False) else "file"
                rows.append(f"{kind:4} {entry.name}")
        return str(directory) + "\n" + "\n".join(sorted(rows))

    def read_file(self, path, start_line=1, lines=100) -> str:
        if not 1 <= start_line <= 100000 or not 1 <= lines <= 160:
            raise ValueError("Use a positive start line and 1–160 lines")
        resolved = self.resolve_path(path, etc=True)
        text = self.read_text(resolved)
        return str(resolved) + "\n" + "\n".join(f"{n}: {line}" for n, line in
                  enumerate(text.splitlines(), 1) if start_line <= n < start_line + lines)

    def search_files(self, query, path=".") -> str:
        if not query or len(query) > 200:
            raise ValueError("Search text must have 1–200 characters")
        root = self.resolve_path(path)
        hits, scanned = [], 0
        started = time.monotonic()
        for directory, children, files in os.walk(root, followlinks=False):
            children[:] = [d for d in children if not d.startswith(".") and d not in
                           ("node_modules", "venv", "__pycache__", "target", "build")]
            for filename in files:
                scanned += 1
                if scanned > 400 or len(hits) >= 40 or time.monotonic() - started > 3:
                    return "\n".join(hits) + "\n[Search limit reached]"
                candidate = Path(directory) / filename
                if candidate.is_symlink():
                    continue
                try:
                    data = self.read_text(candidate, 64000)
                except (ValueError, OSError):
                    continue
                for number, line in enumerate(data.splitlines(), 1):
                    if query.casefold() in line.casefold():
                        hits.append(f"{candidate.relative_to(root)}:{number}: {line[:300]}")
                        if len(hits) >= 40:
                            break
        return "\n".join(hits) or "No matches in the searched files."

    async def command(self, argv, cwd=None) -> str:
        binary = shutil.which(argv[0], path="/usr/bin:/bin")
        if not binary:
            raise ValueError(f"{argv[0]} is not installed")
        env = {k: os.environ[k] for k in ("HOME", "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS") if k in os.environ}
        env.update(PATH="/usr/bin:/bin", LANG="C.UTF-8", LC_ALL="C.UTF-8", GIT_CONFIG_NOSYSTEM="1",
                   GIT_CONFIG_GLOBAL="/dev/null", GIT_TERMINAL_PROMPT="0", GIT_OPTIONAL_LOCKS="0",
                   GIT_PAGER="cat", PAGER="cat", SYSTEMD_PAGER="cat", SYSTEMD_PAGERSECURE="1")
        proc = await asyncio.create_subprocess_exec(binary, *argv[1:], cwd=cwd, env=env,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, start_new_session=True)
        data = bytearray()
        truncated = False
        try:
            async with asyncio.timeout(6):
                while True:
                    chunk = await proc.stdout.read(1024)
                    if not chunk:
                        break
                    data.extend(chunk)
                    if len(data) > 10000:
                        truncated = True
                        break
                if not truncated:
                    await proc.wait()
        finally:
            if proc.returncode is None:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                await proc.wait()
        return data[:10000].decode(errors="replace") + ("\n[Output truncated]" if truncated else "")

    async def git_inspect(self, view, path=".") -> str:
        options = {
            "status": ["status", "--short", "--branch", "--untracked-files=normal"],
            "diff": ["diff", "--no-ext-diff", "--no-textconv", "--no-renames", "--ignore-submodules=all"],
            "log": ["log", "--no-show-signature", "-8", "--format=%h %s"],
        }
        if view not in options:
            raise ValueError("Choose status, diff or log")
        directory = self.resolve_path(path)
        return await self.command(["git", "--no-pager", "--no-optional-locks", "-c", "core.fsmonitor=false",
                                   "-c", "core.hooksPath=/dev/null", *options[view]], cwd=directory)

    def read_system_info(self) -> str:
        mem = {}
        for line in Path("/proc/meminfo").read_text().splitlines():
            key, value = line.split(":", 1)
            if key in ("MemTotal", "MemAvailable", "SwapTotal", "SwapFree"):
                mem[key + "_MiB"] = round(int(value.split()[0]) / 1024)
        disk = shutil.disk_usage(self.cwd)
        return json.dumps({"hostname": platform.node(), "os": platform.platform(), "cpus": os.cpu_count(),
                           "load_1_5_15": os.getloadavg(), "memory": mem,
                           "disk_path": str(self.cwd), "disk_free_GiB": round(disk.free / 2**30, 1),
                           "disk_total_GiB": round(disk.total / 2**30, 1)}, indent=2)

    async def list_processes(self, name="") -> str:
        if name and not re.fullmatch(r"[A-Za-z0-9_.+-]{1,80}", name):
            raise ValueError("Give a single process name, such as pacman")
        selection = ["-C", name] if name else ["-e"]
        return await self.command(["ps", *selection, "-o", "pid,ppid,user,tty,comm,pcpu,pmem", "--sort=-pcpu"])

    async def inspect_process(self, pid) -> str:
        if type(pid) is not int or not 1 <= pid <= 2147483647:
            raise ValueError("Give a positive process ID")
        chain, seen = [], set()
        status = "parent chain limit reached"
        while pid and pid not in seen and len(chain) < 8:
            seen.add(pid)
            output = await self.command(["ps", "-p", str(pid), "-o",
                                        "pid=,ppid=,user:64=,tty=,stat=,etimes=,comm="])
            if not output.strip():
                status = f"PID {pid} exited or is not visible"
                break
            fields = output.strip().split(None, 6)
            if len(fields) != 7 or not all(fields[i].isdigit() for i in (0, 1, 5)):
                raise ValueError("Process details were unavailable")
            if int(fields[0]) != pid:
                raise ValueError("Process identity changed during inspection")
            chain.append(dict(pid=pid, ppid=int(fields[1]), owner=fields[2], tty=fields[3],
                              state=fields[4], age_seconds=int(fields[5]), name=fields[6]))
            pid = int(fields[1])
        if not pid:
            status = "top of visible process tree"
        return json.dumps({"chain_from_process_to_parents": chain, "status": status}, indent=2)

    async def service_status(self, unit, user=False) -> str:
        if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.@:-]{0,150}", unit):
            raise ValueError("Give one unit name, such as nginx.service")
        scope = ["--user"] if user else []
        state = await self.command(["systemctl", *scope, "show", "--no-pager",
                "--property=Id,LoadState,ActiveState,SubState,Result,ExecMainStatus,FragmentPath", "--", unit])
        logs = await self.command(["journalctl", *scope, "--no-pager", "-n", "30", "-o", "short-iso", "--unit=" + unit])
        return state + "\nRecent logs:\n" + logs

    def read_terminal(self, pane) -> str:
        peer = terminal.pane_by_id(pane)
        if peer["task"] != self.task_id or peer["paused"] == "1":
            raise ValueError("Only unpaused terminals linked to this task can be read")
        return terminal.capture(pane, 100)
