# /etc/bash_completion.d/minecraft
# Bash completion for the minecraft admin wrapper.
# Installed by setup.sh; sourced automatically on interactive shells via bash-completion.

_minecraft_complete() {
    local cur="${COMP_WORDS[COMP_CWORD]}"
    local subcommands="status start stop screen reprovision autoshutdown"

    if [[ ${COMP_CWORD} -eq 1 ]]; then
        COMPREPLY=( $(compgen -W "$subcommands" -- "$cur") )
        return
    fi

    local subcmd="${COMP_WORDS[1]}"
    local instances
    instances=$(systemctl list-unit-files --no-legend 'minecraft-*.service' 2>/dev/null \
        | awk '{print $1}' \
        | grep -v '^minecraft-autoshutdown\.service$' \
        | sed 's/^minecraft-//;s/\.service$//')

    case "$subcmd" in
        status)
            # Offer instance names and --yaml; suppress --yaml once already present
            local yaml_used=false
            local w
            for w in "${COMP_WORDS[@]}"; do
                [[ "$w" == "--yaml" ]] && yaml_used=true
            done
            local candidates="$instances"
            $yaml_used || candidates="$candidates --yaml"
            COMPREPLY=( $(compgen -W "$candidates" -- "$cur") )
            ;;
        start|stop|screen)
            COMPREPLY=( $(compgen -W "$instances" -- "$cur") )
            ;;
        autoshutdown)
            if [[ ${COMP_CWORD} -eq 2 ]]; then
                COMPREPLY=( $(compgen -W "status logs run enable disable" -- "$cur") )
            elif [[ ${COMP_CWORD} -ge 3 && "${COMP_WORDS[2]}" == "run" ]]; then
                # --dry-run is the only option; suppress once already present
                local dry_run_used=false
                local w
                for w in "${COMP_WORDS[@]}"; do
                    [[ "$w" == "--dry-run" ]] && dry_run_used=true
                done
                $dry_run_used || COMPREPLY=( $(compgen -W "--dry-run" -- "$cur") )
            fi
            ;;
    esac
}

complete -F _minecraft_complete minecraft
