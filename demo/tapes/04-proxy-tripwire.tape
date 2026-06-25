# 04 - The proxy tripwire
# ---------------------------------------------------------------------------
# The rug pull, but for a client that can't run the import (Claude Code, Zed).
# A tiny client connects *through* `kiji-safeguard proxy` to a third-party
# `weather` server. First sight verifies and forwards the real tool; then a
# tampered build of the same server ships and the proxy serves a tripwire --
# so all the model ever sees is a blocked notice. The poisoned tool never
# arrives. Pairs with the "Using it with Claude Code, Zed" README section.

Output demo/gif/proxy-tripwire.gif

Source demo/tapes/_common.tape

# 1. A client connects through the proxy to the clean 'weather' server.
#    First sight registers it; the real tool is forwarded unchanged.
Type "python demo/fixtures/proxy_client.py demo/fixtures/proxy_upstream.py"
Sleep 700ms
Enter
Sleep 2.6s

# 2. A tampered build of the SAME 'weather' server ships: its description is
#    poisoned (exfiltrate ~/.ssh/id_rsa) and its input schema widened.
Type "# a tampered build of the same 'weather' server ships..."
Sleep 500ms
Enter
Sleep 700ms

# 3. The client runs in strict mode. The proxy verifies the live interface,
#    sees the hash diverge, and serves a tripwire instead of the real tools.
Type "export KIJI_SAFEGUARD_MODE=verify KIJI_SAFEGUARD_ENFORCE=1"
Sleep 400ms
Enter
Sleep 500ms
Type "KIJI_DEMO_TAMPER=1 python demo/fixtures/proxy_client.py demo/fixtures/proxy_upstream.py"
Sleep 700ms
Enter
Sleep 4s
