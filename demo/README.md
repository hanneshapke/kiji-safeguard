# kiji-safeguard demo tapes

This directory holds the **VHS demo tapes** that produce the terminal GIFs for
the README and the [landing page](../landing_page/index.html), plus everything
needed to render them reproducibly. [VHS](https://github.com/charmbracelet/vhs)
("video hardware shell") turns a small `.tape` script into a GIF, so the demos
are code: versioned, reviewable, and re-rendered from one command when the CLI
output changes.

```
demo/
├── README.md              # this file — the demo design & outline
├── render.sh              # render the tapes → demo/gif/*.gif (used by humans + CI)
├── Makefile               # make / make hero / make clean
├── tapes/
│   ├── _common.tape       # shared theme, fonts, and venv/registry setup
│   ├── 01-rug-pull.tape   # HERO: register → verify → tamper → fail
│   ├── 02-magic-import.tape  # the one-liner: first sight → verified → refuse
│   └── 03-cli-tour.tape   # hash / show-interface / register / verify
├── fixtures/
│   ├── weather_server.py          # clean, reviewed interface (mcp-only, no keys)
│   ├── weather_server_tampered.py # same name, poisoned description + widened schema
│   └── serve_once.py              # fire the magic-import hook once, then exit
└── gif/
    ├── rug-pull.svg       # static poster (committed; renders before any GIF exists)
    └── *.gif              # rendered output (run `make`)
```

## The story we're telling

The product is one idea — *hash an MCP server's public interface, register it
once, and recompute it on every run; if a name, description or schema changes,
the hash changes and verification fails loudly*. The demos dramatise exactly
that, smallest surface first, using a throwaway `weather` server that depends
only on `mcp` (no OpenAI key, no Yahoo Finance, no network), so anyone — and
CI — can render them.

| # | Tape | Where it goes | Beat-by-beat |
|---|------|---------------|--------------|
| 01 | **`01-rug-pull.tape`** — the hero | Top of README, landing-page hero | **register** the reviewed `weather` interface → **verify** it (seal holds, green `OK`) → a *tampered build of the same server* ships → **verify** fails (red `FAILED`, the diff of the poisoned description + new param, two diverging hashes) → `exit code: 1`, ready to fail a CI gate. |
| 02 | **`02-magic-import.tape`** — the one-liner | "The magic one-liner" section | `grep` shows the single `import kiji_safeguard.autosign` line → **first run** registers (trust-on-first-use) → **second run** verifies → tamper a tool and run under `KIJI_SAFEGUARD_ENFORCE=1` → the server **refuses to start**. |
| 03 | **`03-cli-tour.tape`** — the CLI | Quickstart / docs | `hash` (no registry) → `hash --show-interface` (what actually gets hashed) → `register` → `verify`. The four verbs in one pass. |

### Why these three, and not more

- **The rug pull is the whole product in 25 seconds.** It's the one GIF that
  has to exist; everything else is supporting material. It earns the top of the
  README.
- **The one-liner answers "what do *I* have to do?"** — nothing but an import.
- **The CLI tour serves the "I want explicit control / a CI gate" reader.**
- **Approval mode is deliberately *not* a tape.** It is a human-in-the-loop
  flow whose payoff is the registry's **Pending Approvals** panel in the
  browser — a terminal recording can't show the click that matters. Capture it
  as a short screen recording of the web UI (the diff appearing, **Approve**
  being clicked, the paused agent resuming) and drop it next to the others. The
  terminal half alone ("waiting up to 1800s…") is an anticlimax.

## Look and feel

`tapes/_common.tape` pins a palette taken straight from the landing page so the
GIFs sit naturally beside it:

| Role | Colour | |
|------|--------|--|
| Background (aizome indigo-black) | `#0c121d` | |
| Foreground (snow) | `#edf0f5` | |
| `register` / `OK` / verified (jade) | `#4fa37c` | |
| `FAILED` / warnings / cursor (hanko vermilion) | `#e0492e` | |
| Hashes (gold) | `#d9b36a` | |
| Comments & metadata (fog) | `#6b7689` | |

1280×720, generous padding, ~45 ms typing speed, JetBrains Mono. Keep new
scenes sourcing `_common.tape` so every GIF stays visually consistent.

## Rendering

You need [`vhs`](https://github.com/charmbracelet/vhs) and
[`uv`](https://docs.astral.sh/uv/) on `PATH`. From the repository root:

```bash
make -C demo            # render all three GIFs into demo/gif/
make -C demo hero       # just the rug-pull hero GIF
./demo/render.sh 02-magic-import   # one tape by name
```

`render.sh` does the fiddly bits so the tapes stay clean:

1. creates a throwaway venv (`.venv-demo`, git-ignored) with the `dev` extras;
2. for **each** tape, starts a *fresh* registry on `127.0.0.1:8000` (the default
   port, so the tapes need no `--registry` flag) backed by an empty temp SQLite
   DB — so "first sight → registered" is reproducible every run, never "already
   registered";
3. runs `vhs` from the repo root (so the relative paths in the tapes resolve)
   and tears the registry down afterwards.

> **Determinism note.** Hashes are content hashes, so they're stable across
> machines. `serve_once.py` stubs the stdio transport after the safeguard hook
> fires, so tape 02 never blocks on a real server. If a future `mcp` release
> changes that internal, fall back to `python weather_server.py < /dev/null`
> (run the real server, feed it EOF) — the hook output is identical.

## Wiring the GIFs into the README and landing page

The committed **`gif/rug-pull.svg`** is a static poster so the hero image is
never broken before anyone renders a GIF. The README currently points at it;
once you've run `make -C demo` and committed `gif/rug-pull.gif`, swap the
reference to the animated version:

```markdown
<!-- README.md, near the top -->
![kiji-safeguard catching a tampered MCP server](demo/gif/rug-pull.gif)
```

For the **landing page** (`landing_page/index.html`), the hero already has a
hand-built static terminal and the demo section has an interactive tamper
widget — both worth keeping. The natural home for the recorded GIF is *between*
them, as a "watch the real thing" band at the end of the `#demo` section:

```html
<figure style="margin-top:40px">
  <img src="https://raw.githubusercontent.com/hanneshapke/kiji-safeguard/main/demo/gif/rug-pull.gif"
       alt="kiji-safeguard registering a server, verifying it, then catching a tampered build"
       style="width:100%;border:1px solid #dad3c4;border-radius:10px" />
  <figcaption style="color:#5c554b;font-size:14px;margin-top:10px">
    The same flow in your terminal: register, verify, and the seal breaking on a tampered build.
  </figcaption>
</figure>
```

(The landing page is served from `main` via GitHub Pages, so it can use the
absolute `raw.githubusercontent.com/...//main/...` URL, which also renders on
PyPI. Relative paths are fine for the README on GitHub.)

## CI

`.github/workflows/demo.yml` renders the tapes on demand
(`workflow_dispatch`) using the official `charmbracelet/vhs-action`, then
uploads the GIFs as an artifact. It's intentionally manual — re-render and
commit the GIFs when CLI output changes, rather than on every push. Flip it to
auto-commit if you'd rather the GIFs track `main` automatically.
