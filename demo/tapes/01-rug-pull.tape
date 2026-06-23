# 01 - The rug pull (HERO GIF)
# ---------------------------------------------------------------------------
# The whole pitch in ~25 seconds: publish a reviewed MCP interface, verify it
# (the seal holds), then a tampered build of the same server ships and
# verification fails loudly with a diff and a non-zero exit -- ready to fail a
# CI gate. This is the GIF for the top of the README and the landing page.

Output demo/gif/rug-pull.gif

Source demo/tapes/_common.tape

# 1. Publish the reviewed interface (what a server or its CI does on release).
Type "kiji-safeguard register demo/fixtures/weather_server.py"
Sleep 700ms
Enter
Sleep 1.8s

# 2. Every later run verifies it -- the hashes match, the seal holds.
Type "kiji-safeguard verify demo/fixtures/weather_server.py"
Sleep 700ms
Enter
Sleep 2s

# 3. A tampered build of the SAME 'weather' server ships: a tool description
#    rewritten to poison the model, plus a widened input schema.
Type "# someone ships a tampered build of the same 'weather' server..."
Sleep 500ms
Enter
Sleep 800ms
Type "kiji-safeguard verify demo/fixtures/weather_server_tampered.py"
Sleep 700ms
Enter
Sleep 3s

# 4. Non-zero exit -- a pipeline gate would fail the build right here.
Type 'echo "exit code: $?"'
Sleep 400ms
Enter
Sleep 2.5s
