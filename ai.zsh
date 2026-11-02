# Inline companion: ordinary scrollback, ordinary prompt, no extra panes.
typeset -g _ZSH_AI_DIR="${${(%):-%N}:A:h}"
typeset -g _ZSH_AI_PY="$_ZSH_AI_DIR/zsh_ai.py"
typeset -g _ZSH_AI_PYTHON="$_ZSH_AI_DIR/venv/bin/python"
typeset -gi _zsh_ai_started=0 _zsh_ai_tracking=0 _zsh_ai_fd=-1 _zsh_ai_bg_pid=0
typeset -gi _zsh_ai_output_start=0
typeset -g _zsh_ai_event='' _zsh_ai_last=''
zmodload zsh/datetime

_ai_cmd() { command "$_ZSH_AI_PYTHON" "$_ZSH_AI_PY" "$@" }
alias ai='noglob _ai_cmd'
typeset -g _zsh_ai_watch_default="$(_ai_cmd watch-default 2>/dev/null)"
[[ "$_zsh_ai_watch_default" == (all|errors|off) ]] || _zsh_ai_watch_default=all

_zsh_ai_cancel_watch() {
    if (( _zsh_ai_fd >= 0 )); then
        zle -F "$_zsh_ai_fd" 2>/dev/null
        exec {_zsh_ai_fd}<&-
        _zsh_ai_fd=-1
    fi
    if (( _zsh_ai_bg_pid > 0 )); then
        kill -TERM "$_zsh_ai_bg_pid" 2>/dev/null
        _zsh_ai_bg_pid=0
    fi
    return 0
}

_zsh_ai_notice_ready() {
    local fd="$1" packet=''
    IFS= read -r -u "$fd" packet
    zle -F "$fd" 2>/dev/null
    exec {fd}<&-
    _zsh_ai_fd=-1
    _zsh_ai_bg_pid=0
    [[ -n "$packet" ]] || return 0
    zle -I
    print -rn -- "$packet" | command "$_ZSH_AI_PYTHON" "$_ZSH_AI_PY" render-watch
    zle reset-prompt
}

_zsh_ai_preexec() {
    _zsh_ai_cancel_watch
    _zsh_ai_tracking=0
    [[ "$1" == ai || "$1" == ai\ * || "$1" == _ai_cmd\ * || "$1" == noglob\ _ai_cmd\ * ]] && return 0
    _zsh_ai_tracking=1
    _zsh_ai_last="$1"
    [[ -n "$TMUX_PANE" ]] || return 0
    local position="$(command tmux display-message -p -t "$TMUX_PANE" '#{history_size} #{cursor_y}')"
    local -a coords=( ${(z)position} )
    _zsh_ai_output_start=$(( ${coords[1]:-0} + ${coords[2]:-0} ))
    local line="${1//$'\n'/ }"
    line="${line//$'\t'/ }"
    command tmux set-option -p -t "$TMUX_PANE" @zai_ready 0 \; \
        set-option -p -t "$TMUX_PANE" @zai_command "${line[1,1200]}" 2>/dev/null
    return 0
}

_zsh_ai_precmd() {
    local rc=$?
    [[ -n "$TMUX_PANE" ]] || return 0
    local settings="$(command tmux display-message -p -t "$TMUX_PANE" '#{history_size} #{cursor_y} p#{@zai_paused} w#{@zai_watch}')"
    local -a parts=( ${(z)settings} )
    local paused="${parts[3]#p}" watch_mode="${parts[4]#w}"
    [[ -n "$watch_mode" ]] || watch_mode="$_zsh_ai_watch_default"
    if (( _zsh_ai_tracking || ! _zsh_ai_started )); then
        _zsh_ai_event="$EPOCHREALTIME"
        if (( _zsh_ai_tracking )) && [[ "$paused" != 1 ]]; then
            local start=$(( _zsh_ai_output_start - ${parts[1]:-0} ))
            local end=${parts[2]:-0}
            (( start < -100 )) && start=-100
            (( start > end )) && start=-60
            local output="$(command tmux capture-pane -p -J -t "$TMUX_PANE" -S "$start" -E "$end")"
            command tmux set-option -p -t "$TMUX_PANE" @zai_output "${output[-12000,-1]}" 2>/dev/null
        fi
        command tmux set-option -p -t "$TMUX_PANE" @zai_ready 1 \; \
            set-option -p -t "$TMUX_PANE" @zai_exit "$rc" \; \
            set-option -p -t "$TMUX_PANE" @zai_event "$_zsh_ai_event" \; \
            set-option -p -t "$TMUX_PANE" @zai_protocol 3 2>/dev/null
    fi
    if (( _zsh_ai_tracking )) && [[ "$paused" != 1 && "$watch_mode" != off ]] &&
       { [[ "$watch_mode" == all ]] || (( rc != 0 && rc != 130 && rc != 141 )); }; then
        exec {_zsh_ai_fd}< <(exec "$_ZSH_AI_PYTHON" "$_ZSH_AI_PY" watch-event "$_zsh_ai_event" 2>/dev/null)
        _zsh_ai_bg_pid=$!
        zle -F "$_zsh_ai_fd" _zsh_ai_notice_ready
    fi
    _zsh_ai_started=1
    _zsh_ai_tracking=0
    return 0
}

_zsh_ai_ask_widget() {
    _zsh_ai_cancel_watch
    [[ "$BUFFER" == ai\ * ]] || BUFFER="ai $BUFFER"
    CURSOR=$#BUFFER
}

_zsh_ai_insert_widget() {
    local proposed="$(_ai_cmd take-staged)"
    if [[ -n "$proposed" ]]; then
        [[ -n "$BUFFER" ]] && zle push-input
        BUFFER="$proposed"
        CURSOR=$#BUFFER
    else
        zle -M 'No staged command for this terminal and task.'
    fi
}

_zsh_ai_look_widget() {
    BUFFER='ai look'
    zle accept-line
}

autoload -Uz add-zsh-hook
add-zsh-hook -d preexec _zsh_ai_preexec
add-zsh-hook -d precmd _zsh_ai_precmd
add-zsh-hook preexec _zsh_ai_preexec
add-zsh-hook precmd _zsh_ai_precmd
add-zsh-hook zshexit _zsh_ai_cancel_watch
zle -N _zsh_ai_ask_widget
zle -N _zsh_ai_insert_widget
zle -N _zsh_ai_look_widget
bindkey '^Xa' _zsh_ai_ask_widget
bindkey '^Xi' _zsh_ai_insert_widget
bindkey '^Xs' _zsh_ai_look_widget
