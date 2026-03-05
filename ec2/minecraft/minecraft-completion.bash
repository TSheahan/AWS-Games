# /etc/bash_completion.d/minecraft
# Bash completion for the minecraft admin wrapper.
# Installed by setup.sh; sourced automatically on interactive shells via bash-completion.

_minecraft_complete() {
    local cur="${COMP_WORDS[COMP_CWORD]}"
    local subcommands="status start stop screen reprovision"

    if [[ ${COMP_CWORD} -eq 1 ]]; then
        COMPREPLY=( $(compgen -W "$subcommands" -- "$cur") )
        return
    fi

    local subcmd="${COMP_WORDS[1]}"
    local instances
    instances=$(systemctl list-unit-files --no-legend 'minecraft-*.service' 2>/dev/null \
        | awk '{print $1}' | sed 's/^minecraft-//;s/\.service$//')

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
    esac
}

complete -F _minecraft_complete minecraft
