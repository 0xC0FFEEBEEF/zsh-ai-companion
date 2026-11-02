# Inline terminal companion

The assistant lives in normal terminal scrollback. There is no side pane, dashboard,
full-screen application, or startup model greeting. The existing zsh prompt and
terminal colors stay in place. Replies have a small `ai ›` label.

```text
$ cat missing.conf
cat: missing.conf: No such file or directory
ai › That file isn't in this directory. ...
$
```

The example shows the intended placement; replies come from the model and vary.

## Talk and inspect

- `ai what just happened?` asks one question beneath the output.
- Bare `ai` starts an inline conversation. Empty line or Ctrl+D returns to zsh.
- **Ctrl+X a** starts an `ai` question at the current prompt.
- **Ctrl+X s**, or `ai look`, asks about the most recent command and output.
- **Ctrl+C** stops a foreground answer.
- **Ctrl+X i** inserts a suggested command for editing. You press Enter to run it.

The assistant can independently inspect local files and system state through these
read-only tools: `list_directory`, `read_file`, `search_files`, `git_inspect`,
`read_system_info`, `list_processes`, `inspect_process`, `service_status`, and `read_terminal`.
`ai tools` describes them. During an explicit question, a small temporary status
line shows what it is checking. Read-only troubleshooting is already authorized:
the assistant should perform these checks itself instead of handing you equivalent
commands. Commands that change the system are still proposals for you to run.
There is no arbitrary shell tool.

File tools are limited to the linked terminals' working directories, plus non-secret
`/etc` files for direct reads. Credential files, private history, assistant state,
special devices, and paths escaping those roots are excluded. File output is bounded
and common secret assignments are redacted. This is not a complete secret detector.
Git tools disable external diff helpers, textconv, fsmonitor, hooks, and optional index
locks. Process inspection omits command-line arguments and environment variables.
Process lists can filter by exact program name. `inspect_process` follows up to
eight parents, with owner, terminal, state and age, to distinguish a process's
effective owner from the shell or program that launched it. Service tools use fixed
status and journal queries. Tools run on this local machine, not inside SSH sessions.
A foreground question may make at most four inspections; an automatic comment gets
two. Tool results are presented to the model as observed data, not instructions.

## After-output comments

By default, each completed command starts a quiet background check after a short
pause. Your prompt returns immediately. The assistant decides whether the result
merits a comment: actionable warnings or errors, or a diagnostic result that
advances your current conversation. Successful diagnostics can trigger follow-up
inspection through read-only tools. Routine output should stay silent.

If the assistant has useful advice, it prints beneath the output and redraws the
prompt while preserving your input. Silent decisions produce no text or prompt
redraw. An interruption alone should not trigger advice, but a warning printed
before you interrupted the command can still be useful. In `errors` mode,
Ctrl+C/130 and SIGPIPE/141 do not start a check. Another command cancels the previous background check;
outdated responses are discarded. No automatic memories are saved from these checks.

```sh
ai watch all      # default: consider every command, comment only when useful
ai watch errors   # only check failed commands
ai watch off      # speak only when asked
ai pause          # stop observing this pane and using tools from it
ai resume
```

These watch overrides are per terminal. The `watch` setting in the configuration
file sets the default for new shells. The hook snapshots completed command output
before any AI reply. Asking another question keeps that snapshot, preventing the
assistant from treating its own previous answer as fresh shell output. While a
command is running, linked-terminal reads use its current scrollback. Older shells
need the new hook loaded to gain this behavior.

A fresh shell without a completed-command snapshot uses visible scrollback. An
empty saved result still means the last command produced no output; it does not
mean the assistant lacks terminal access. It reads text snapshots on questions
and after watched commands, rather than continuously watching every keystroke.

Normal drag selection and Ctrl+Shift+C copying use Konsole's native selection;
tmux mouse capture is disabled. Konsole tabs running the companion use the inner
directory and process title (`~:zsh` at the home prompt). The title follows the
active pane as you change directories or run a program.

## Tasks, terminals, and memory

Terminals start separately, even in the same directory. Named tasks persist:

```sh
ai task Repair NFS
ai link %1
ai status
ai unlink %1
ai tasks
```

Using the same exact task name in another terminal shares its conversation and notes.
Only explicitly linked terminals have their output sent to the model. Tool access
also enforces that boundary. The shared tmux server remains behind the scenes for
terminal capture and grouping; the assistant no longer splits windows.

```sh
ai remember The API runs on port 8080
ai remember global I prefer concise replies
ai memory
ai forget 3
```

You can also ask it to remember things in conversation. Model-written notes must
include evidence quoted from your messages, terminal output or a tool result, and
are scoped to the current task. Inspect or remove mistaken notes with `ai memory`
and `ai forget`. Global notes apply to all tasks. Existing task history and memories
are preserved in `~/.local/state/zsh-ai/companion.sqlite3`.

## Activate and restore

New terminals load the inline version automatically. In an already-open shell:

```sh
source ~/.zsh/ai/ai.zsh
```

Leave an old `ai` conversation with Ctrl+D before running that shell command.
Model endpoints and the model selection stay in `~/.config/zsh-ai/config.json`.
The private Python environment remains `~/.zsh/ai/venv`.

The installer creates a dated backup and prints its path. To restore the previous
files, run `python3 <backup>/restore.py`, then open a new terminal. Conversation and
memory data are left intact. [Installation record](INSTALLATION.md) records the
actual backup paths on this machine.

## Validation

Run both test files with pytest and pytest-asyncio. Set `ZAI_TEST_INSTALL` to an
isolated installation directory to enable the real zsh/tmux integration test.
Tests cover task isolation, memory, command staging, structured replies, real
read-only file and Git operations, excluded paths, task-scoped tools, tool budgets,
stale notification suppression, inline chat, prompt input preservation, and lack
of automatic pane creation. Model-host speed and answer quality still vary;
read-only tool use is bounded, but the model can make incorrect interpretations.

## About this copy

A public snapshot of my private working repo; host names stand in for my lab's addresses. More of my projects: [nbdevlab.com](https://www.nbdevlab.com).


## About this copy

A public snapshot of my private working repo; host names stand in for my lab's addresses. More of my projects: [nbdevlab.com](https://www.nbdevlab.com).

## License

GNU General Public License v3.0 or later, see [`LICENSE`](LICENSE).

<sub>Preparing this public copy from my private repo was done with help from Claude.</sub>
