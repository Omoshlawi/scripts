# pdfpass.py

Add, change, or remove the open password on a PDF. The result is written to `out/`;
the input file is never modified.

```sh
./pdfpass.py statement.pdf                                    # interactive, asks everything
./pdfpass.py -m remove  -p 1234 -o open.pdf    statement.pdf  # strip the password
./pdfpass.py -m change  -p 1234 -n new  -o rotated.pdf statement.pdf
./pdfpass.py -m encrypt -n secret -o locked.pdf report.pdf    # protect a plain PDF
```

Every input is available as a flag, and anything you leave out is prompted for — so
`./pdfpass.py file.pdf` walks you through it, and a full command line runs unattended.

## First run

The script installs its own dependency the first time you run it:

```
First run: creating /Users/omosh/projects/scripts/.venv and installing pikepdf...
```

It creates `.venv/`, pip-installs [pikepdf](https://pikepdf.readthedocs.io), and
re-executes itself inside that environment. This happens once; later runs start
immediately. Nothing is installed system-wide.

## Modes

| Mode | For | Behaviour |
| --- | --- | --- |
| `encrypt` (alias `add`) | a PDF with **no** password | Encrypts it with a password you choose. Refuses a file that is already protected. |
| `change` | a **protected** PDF | Replaces the existing password with a new one. On an unprotected file it encrypts, same as `encrypt`. |
| `remove` | a **protected** PDF | Strips the password entirely. On an unprotected file it says so and exits 0 without writing. |

Mismatches are caught with a message that points at the right mode:

```
$ ./pdfpass.py -m encrypt -n x -o y.pdf statement.pdf
error: statement.pdf is already password protected; use --mode change to replace the password, or --mode remove to strip it
```

## Options

| Flag | Meaning |
| --- | --- |
| `input` | The PDF to work on (positional, required). |
| `-p`, `--password PASS` | Current open password / PIN. Prompted (hidden) if omitted. |
| `-m`, `--mode {encrypt,change,remove}` | What to do. `add` is accepted as a synonym for `encrypt`. Prompted if omitted. |
| `-n`, `--new-password PASS` | New password, for `encrypt` and `change`. Prompted twice (hidden, with confirmation) if omitted. |
| `--owner-password [PASS]` | Set a separate owner password for full rights. Pass the flag **with no value** to be prompted for it. Omit it entirely and the new password is used for both. |
| `--no-print` | Disallow printing in the output. |
| `--no-copy` | Disallow copying text out of the output. |
| `-o`, `--output PATH` | Output file. A bare name goes into `out/`. Prompted (with a default) if omitted. |
| `-f`, `--force` | Overwrite the output file if it already exists. |
| `-h`, `--help` | Usage. |

## Interactive mode

Omitting a flag is how you get a prompt, and passwords are always read hidden. The
prompts adapt to the file.

**A protected PDF** gets the full menu:

```
$ ./pdfpass.py statement.pdf

What should I do with the password?
  1) Remove it entirely
  2) Change it to a new one
Choice [1]: 1
Password (PIN):
Output file name [statement-unlocked.pdf]:
Password removed -> /Users/omosh/projects/scripts/out/statement-unlocked.pdf
```

**An unprotected PDF** has only one sensible action, so it just confirms:

```
$ ./pdfpass.py report.pdf

report.pdf isn't password protected.
Add a password to it? [Y/n]: y
New password:
Confirm new password:
Output file name [report-protected.pdf]:
Password added -> /Users/omosh/projects/scripts/out/report-protected.pdf
```

Answering `n` prints `Nothing to do.` and exits 0.

## Output

- A bare `--output` name lands in `out/` — `-o locked.pdf` writes `out/locked.pdf`.
- A name containing a `/` is used as given: `-o ~/Desktop/locked.pdf` goes to the Desktop.
  Missing directories are created.
- `.pdf` is appended if the name lacks it, so `-o locked` is fine.
- With no `--output`, the prompt offers a default you can accept with Enter:
  `<name>-unlocked.pdf` (remove), `<name>-relocked.pdf` (change), `<name>-protected.pdf` (encrypt).
- Existing files are never overwritten silently — you get a `[y/N]` prompt when
  interactive, or an error telling you to use `--force`.
- Writing over the input file is always refused, `--force` included.
- The file is written to a temporary name first and moved into place, so an
  interrupted run cannot leave a half-written PDF.

## Encryption details

Output is always **AES-256** (`/R 6`), regardless of what the input used — M-PESA
statements, for example, arrive as RC4-128, and a `change` on one is an upgrade.

Without `--owner-password`, the new password is set as both the user and owner
password: one secret, full rights. With it you get the two-password arrangement —
the user password opens the file, the owner password grants everything.

`--no-print` and `--no-copy` set the PDF permission bits, which is what makes a
separate owner password mean anything: readers gate *actions* on those bits, so with
everything allowed a second password has no visible effect. **These restrictions are
advisory.** Mainstream readers honour them; any tool that can open the file can strip
them. Treat them as a speed bump, not protection.

## Security notes

- A password passed as `--password`/`--new-password`/`--owner-password` ends up in
  your shell history and is visible in `ps` while the script runs. Omit the flag to
  get a hidden prompt instead — that's the safe default.
- `--mode remove` produces an unprotected PDF sitting on disk in `out/`. That folder
  is gitignored, but it is not otherwise protected; delete what you don't need.
- Passwords are never written to disk or logged by the script.

## Exit codes

| Code | Meaning |
| --- | --- |
| 0 | Success, or nothing to do (unprotected file with `remove`, or you declined the prompt). |
| 1 | Wrong password, bad input, refused overwrite, or a mode that doesn't fit the file. |
| 2 | Bad usage — unknown flag, missing input (argparse). |

## Troubleshooting

**`Wrong password.`** — When you typed it at the prompt you get three attempts. When
it came from `-p` you get one, so the script can't sit there guessing in a script or
cron job. For M-PESA statements the password is the PIN Safaricom sends with the
statement email.

**`error: no --mode given and stdin is not a terminal, so I can't ask for it`** —
The script was run without a terminal (piped, cron, CI) and something it needed
wasn't supplied. Pass the missing flag explicitly; it will never hang waiting for
input that can't come.

**The first run fails while installing pikepdf** — Delete `.venv/` and run again:

```sh
rm -rf .venv && ./pdfpass.py --help
```

## Worked example: an M-PESA statement

Safaricom emails statements encrypted with your PIN. To keep an open copy:

```sh
./pdfpass.py MPESA_Statement_2026-09-04_to_2025-09-04_254793889658.pdf
```

Choose `1` to remove the password, enter the PIN at the hidden prompt, press Enter to
accept the default output name, and the unlocked copy appears in `out/`. To keep it
protected but with a password you'll actually remember, choose `2` instead.
