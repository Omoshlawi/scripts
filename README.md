# scripts

A collection of standalone command-line utilities. Each script is self-contained —
drop it anywhere, run it directly, no project-wide setup — and each one has its own
page in [`docs/`](docs/).

## Scripts

| Script | What it does | Docs |
| --- | --- | --- |
| [`pdfpass.py`](pdfpass.py) | Add, change, or remove a PDF's open password | [docs/pdfpass.md](docs/pdfpass.md) |

## Requirements

macOS or Linux with Python 3 (this machine uses the system Python, 3.9.6 at
`/usr/bin/python3`). Nothing is installed globally: a script that needs packages
creates its own `.venv/` on first run and re-executes itself inside it, so the first
invocation is slower and every one after that is instant. `requirements.txt` records
the resulting pins.

## Layout

```
.              scripts live at the root, one file each
docs/          one page per script, plus _template.md
out/           generated output (gitignored)
.venv/         auto-created on first run (gitignored)
```

`.gitignore` covers `.venv/`, `out/`, `*.pdf`, and `__pycache__/`, so generated files
and the documents you feed these tools stay out of version control.

## Adding a script

1. Put `name.py` at the root with a `#!/usr/bin/env python3` shebang, then
   `chmod +x name.py` so it runs as `./name.py`.
2. If it needs third-party packages, copy the `_bootstrap()` function from
   [`pdfpass.py`](pdfpass.py) — it creates `.venv/`, pip-installs what's missing, and
   re-execs into it — then refresh the pins with
   `.venv/bin/pip freeze > requirements.txt`.
3. Copy [`docs/_template.md`](docs/_template.md) to `docs/name.md` and fill it in.
   Delete any heading that doesn't apply.
4. Add a row to the **Scripts** table above.
5. Write output to `out/` rather than next to the input, and never overwrite the
   input file.
