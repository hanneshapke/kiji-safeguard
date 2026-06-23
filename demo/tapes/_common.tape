# Shared look-and-feel for every kiji-safeguard demo tape.
#
# Each tape sets its own `Output ...` first, then `Source`s this file. The
# palette mirrors the project's landing page: aizome indigo-black background,
# hanko-vermilion accents, jade for "verified", gold for hashes.
#
# Requires the demo venv created by ./render.sh (.venv-demo at the repo root)
# and a registry on 127.0.0.1:8000 — the default for both the CLI and the
# autosign hook, so the tapes need no --registry flags or env vars. render.sh
# starts a fresh registry per tape. VHS runs these from the repository root.

Set Shell "bash"
Set FontFamily "JetBrains Mono, Menlo, monospace"
Set FontSize 20
Set Width 1280
Set Height 720
Set Padding 28
Set Margin 24
Set BorderRadius 10
Set TypingSpeed 45ms
Set PlaybackSpeed 1.0

Set Theme { "name": "kiji-safeguard", "background": "#0c121d", "foreground": "#edf0f5", "cursor": "#e0492e", "selection": "#232f44", "black": "#121a28", "red": "#e0492e", "green": "#4fa37c", "yellow": "#d9b36a", "blue": "#6b8fc7", "magenta": "#b08cc7", "cyan": "#5fb3b3", "white": "#edf0f5", "brightBlack": "#3a465c", "brightRed": "#f06a51", "brightGreen": "#6fbf97", "brightYellow": "#e6c789", "brightBlue": "#8aa9d8", "brightMagenta": "#c5a6d6", "brightCyan": "#86c7c7", "brightWhite": "#ffffff" }

# Quietly enter the demo venv and set a clean prompt, then clear so the
# recording opens on a fresh line. The registry runs on the default port, so
# no KIJI_SAFEGUARD_REGISTRY is needed.
Hide
Type "source .venv-demo/bin/activate" Enter
Type "export PS1='\[\e[38;5;245m\]kiji \[\e[38;5;174m\]› \[\e[0m\]'" Enter
Type "clear" Enter
Sleep 600ms
Show
