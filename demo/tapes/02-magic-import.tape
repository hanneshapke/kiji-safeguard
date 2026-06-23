# 02 - The magic one-liner
# ---------------------------------------------------------------------------
# You don't even need the CLI. One import in your MCP server registers it on
# first sight (trust-on-first-use) and verifies it on every run after. Tamper
# with a tool and run under enforce mode -> the server refuses to start.

Output demo/gif/magic-import.gif

Source demo/tapes/_common.tape

# The entire integration is one line near the top of the server.
Type "# the whole integration -- one import:"
Sleep 400ms
Enter
Type "grep -n kiji_safeguard demo/fixtures/weather_server.py"
Sleep 500ms
Enter
Sleep 2s

# First run: trust-on-first-use registers the live interface.
Type "# first run -> trust-on-first-use registers it"
Sleep 400ms
Enter
Type "python demo/fixtures/serve_once.py"
Sleep 500ms
Enter
Sleep 2s

# Run it again: now it verifies against what was registered.
Type "# run it again -> verified"
Sleep 400ms
Enter
Type "python demo/fixtures/serve_once.py"
Sleep 500ms
Enter
Sleep 2s

# Tamper with the tool and enforce strictly -> the server refuses to start.
Type "# tamper with a tool, enforce strictly -> it refuses to start"
Sleep 400ms
Enter
Type "KIJI_SAFEGUARD_ENFORCE=1 KIJI_DEMO_TAMPER=1 python demo/fixtures/serve_once.py"
Sleep 500ms
Enter
Sleep 3s
