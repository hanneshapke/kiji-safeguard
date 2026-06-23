# 03 - CLI tour
# ---------------------------------------------------------------------------
# A quick pass over the four CLI verbs for the docs/quickstart: hash an
# interface, inspect it, pin it to the registry, and verify it.

Output demo/gif/cli-tour.gif

Source demo/tapes/_common.tape

# Print the interface hash -- no registry needed.
Type "kiji-safeguard hash demo/fixtures/weather_server.py"
Sleep 500ms
Enter
Sleep 2s

# Inspect exactly what gets hashed (canonical interface JSON).
Type "kiji-safeguard hash --show-interface demo/fixtures/weather_server.py"
Sleep 500ms
Enter
Sleep 2.5s

# Pin the reviewed interface to the registry -- e.g. from CI.
Type "kiji-safeguard register demo/fixtures/weather_server.py"
Sleep 500ms
Enter
Sleep 1.8s

# Verify any time -- exits non-zero on mismatch, so it doubles as a gate.
Type "kiji-safeguard verify demo/fixtures/weather_server.py"
Sleep 500ms
Enter
Sleep 2.5s
