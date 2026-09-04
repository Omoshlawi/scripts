#!/usr/bin/env python3
"""Add, change, or remove the open password on a PDF, writing the result to out/.

Every input can be given as a flag; anything you leave out is prompted for.

  pdfpass.py [-m encrypt|change|remove] [-p PIN] [-n NEW] [-o OUT.pdf] [-f] INPUT.pdf

Security note: passwords passed as --password/--new-password end up in your
shell history and in `ps` output. Omit them and the script asks for them with
a hidden prompt instead.
"""

import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
VENV_DIR = os.path.join(SCRIPT_DIR, ".venv")
BOOTSTRAP_FLAG = "PDFPASS_BOOTSTRAPPED"


def _bootstrap():
    """Create a local venv with pikepdf and re-exec into it."""
    if os.environ.get(BOOTSTRAP_FLAG):
        sys.exit(
            "pikepdf is still missing after bootstrapping. Try deleting\n"
            "  %s\nand running again." % VENV_DIR
        )

    import subprocess

    venv_python = os.path.join(VENV_DIR, "bin", "python")
    if not os.path.exists(venv_python):
        print("First run: creating %s and installing pikepdf..." % VENV_DIR,
              file=sys.stderr)
        import venv

        try:
            venv.EnvBuilder(with_pip=True).create(VENV_DIR)
        except Exception as exc:
            sys.exit("Could not create the virtualenv: %s" % exc)

    try:
        subprocess.check_call(
            [venv_python, "-m", "pip", "install", "--quiet", "--upgrade", "pip"]
        )
        subprocess.check_call(
            [venv_python, "-m", "pip", "install", "--quiet", "pikepdf"]
        )
    except subprocess.CalledProcessError:
        sys.exit(
            "Installing pikepdf failed (see the pip output above).\n"
            "You can retry after deleting %s" % VENV_DIR
        )

    env = dict(os.environ, **{BOOTSTRAP_FLAG: "1"})
    os.execve(venv_python, [venv_python, os.path.abspath(__file__)] + sys.argv[1:], env)


try:
    import pikepdf
except ImportError:
    _bootstrap()

import argparse
import getpass
import tempfile


INTERACTIVE = sys.stdin.isatty()


def die(message, code=1):
    sys.exit("error: %s" % message if code == 1 else message)


PROMPT_FOR_IT = "\0prompt"  # sentinel for `--owner-password` with no value

MODE_ALIASES = {"encrypt": "encrypt", "add": "encrypt",
                "change": "change", "remove": "remove"}


def normalise_mode(value):
    """Let 'add' stand in for 'encrypt'; reject anything else with usage."""
    try:
        return MODE_ALIASES[value.lower()]
    except KeyError:
        raise argparse.ArgumentTypeError(
            "invalid choice: %r (choose from encrypt, change, remove)" % value)


def need_tty(what):
    """Fail loudly instead of hanging when there is no tty to prompt on."""
    if not INTERACTIVE:
        die("no %s given and stdin is not a terminal, so I can't ask for it" % what)


def ask_encrypt(name):
    """The only sensible action on an unprotected file, so just confirm it."""
    print("\n%s isn't password protected." % name)
    if input("Add a password to it? [Y/n]: ").strip().lower().startswith("n"):
        return None
    return "encrypt"


def ask_mode():
    print("\nWhat should I do with the password?")
    print("  1) Remove it entirely")
    print("  2) Change it to a new one")
    while True:
        answer = input("Choice [1]: ").strip() or "1"
        if answer in ("1", "remove", "r"):
            return "remove"
        if answer in ("2", "change", "c"):
            return "change"
        print("Please answer 1 or 2.")


def ask_new_password(label="New password"):
    while True:
        first = getpass.getpass("%s: " % label)
        if not first:
            print("Password can't be empty.")
            continue
        if first == getpass.getpass("Confirm %s: " % (label[0].lower() + label[1:])):
            return first
        print("Passwords don't match, try again.")


def resolve_output(raw, default_name, force):
    """Bare names land in out/; anything with a slash is used as given."""
    name = raw.strip()
    if not name.lower().endswith(".pdf"):
        name += ".pdf"
    path = name if os.sep in name else os.path.join("out", name)
    path = os.path.abspath(path)

    parent = os.path.dirname(path)
    try:
        os.makedirs(parent, exist_ok=True)
    except OSError as exc:
        die("can't create %s: %s" % (parent, exc))

    if os.path.exists(path) and not force:
        if INTERACTIVE:
            if not input("%s exists. Overwrite? [y/N]: " % path).strip().lower().startswith("y"):
                die("aborted; nothing written")
        else:
            die("%s already exists (use --force to overwrite)" % path)
    return path


def probe(path):
    """Return (has_encryption, opens_with_empty_password) without asking anything."""
    try:
        with pikepdf.open(path) as pdf:
            return pdf.is_encrypted, True
    except pikepdf.PasswordError:
        return True, False
    except pikepdf.PdfError as exc:
        die("could not read %s: %s" % (path, exc))


def open_pdf(path, password, password_from_flag):
    """Open the PDF, re-prompting for the password when we're on a terminal."""
    tries = 0
    while True:
        try:
            return pikepdf.open(path, password=password or "")
        except pikepdf.PasswordError:
            tries += 1
            can_retry = INTERACTIVE and not password_from_flag and tries < 3
            print("Wrong password.", file=sys.stderr)
            if not can_retry:
                sys.exit(1)
            password = getpass.getpass("Password (PIN): ")
        except pikepdf.PdfError as exc:
            die("could not read %s: %s" % (path, exc))


def save(pdf, out_path, new_password, owner_password=None, allow=None):
    """Save via a temp file in the destination dir so a crash can't truncate."""
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(out_path), suffix=".pdf")
    os.close(fd)
    try:
        if new_password is None:
            pdf.save(tmp)
        else:
            # Encryption is a namedtuple, so allow must go in the constructor.
            kwargs = {"allow": allow} if allow is not None else {}
            pdf.save(tmp, encryption=pikepdf.Encryption(
                user=new_password,
                owner=owner_password or new_password,
                R=6,
                **kwargs
            ))
        os.replace(tmp, out_path)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def main():
    parser = argparse.ArgumentParser(
        prog="pdfpass.py",
        description="Add, change, or remove a PDF's open password. "
                    "Anything you don't pass as a flag is prompted for.",
        epilog="Passwords given as flags land in your shell history and in ps "
               "output; omit them to get a hidden prompt instead.",
    )
    parser.add_argument("input", help="the PDF to work on")
    parser.add_argument("-p", "--password", help="current open password / PIN")
    parser.add_argument("-m", "--mode", type=normalise_mode,
                        metavar="{encrypt,change,remove}",
                        help="encrypt an unprotected PDF, change its password, "
                             "or remove it ('add' also means encrypt)")
    parser.add_argument("-n", "--new-password",
                        help="new password (--mode encrypt or --mode change)")
    parser.add_argument("--owner-password", nargs="?", const=PROMPT_FOR_IT,
                        metavar="PASS",
                        help="set a separate owner password for full rights; "
                             "pass the flag with no value to be prompted. "
                             "Without it, the new password is used for both.")
    parser.add_argument("--no-print", action="store_true",
                        help="disallow printing (advisory: honoured by most "
                             "readers, trivially stripped by any tool)")
    parser.add_argument("--no-copy", action="store_true",
                        help="disallow copying text out (advisory, as --no-print)")
    parser.add_argument("-o", "--output",
                        help="output file; a bare name goes into out/")
    parser.add_argument("-f", "--force", action="store_true",
                        help="overwrite the output file if it exists")
    args = parser.parse_args()

    src = os.path.abspath(args.input)
    if not os.path.isfile(src):
        die("no such file: %s" % args.input)

    encrypted, opens_blank = probe(src)

    # A file that still opens on a blank password is unprotected in practice.
    protected = encrypted and not opens_blank

    mode = args.mode
    if mode is None:
        need_tty("--mode")
        mode = ask_mode() if protected else ask_encrypt(args.input)
        if mode is None:
            print("Nothing to do.")
            return 0

    if mode == "encrypt" and protected:
        die("%s is already password protected; use --mode change to replace "
            "the password, or --mode remove to strip it" % args.input)
    if mode == "remove" and args.owner_password:
        die("--owner-password makes no sense with --mode remove")

    if not encrypted:
        if mode == "remove":
            print("%s has no password — nothing to remove." % args.input)
            return 0
        if mode == "change":
            print("%s has no password yet; it will be encrypted with the new one."
                  % args.input)
        password = ""
    elif opens_blank:
        # Encrypted, but the open password is blank (owner-password only).
        password = args.password or ""
    else:
        password = args.password
        if password is None:
            need_tty("--password")
            password = getpass.getpass("Password (PIN): ")

    new_password = None
    owner_password = None
    allow = None
    if mode in ("change", "encrypt"):
        new_password = args.new_password
        if new_password is None:
            need_tty("--new-password")
            new_password = ask_new_password()
        elif not new_password:
            die("--new-password can't be empty")

        owner_password = args.owner_password
        if owner_password == PROMPT_FOR_IT:
            need_tty("a value for --owner-password")
            owner_password = ask_new_password("Owner password")

        if args.no_print or args.no_copy:
            allow = pikepdf.Permissions(
                extract=not args.no_copy,
                print_lowres=not args.no_print,
                print_highres=not args.no_print,
            )

    stem = os.path.splitext(os.path.basename(src))[0]
    suffix = {"remove": "unlocked", "change": "relocked", "encrypt": "protected"}[mode]
    default_name = "%s-%s.pdf" % (stem, suffix)
    raw_output = args.output
    if raw_output is None:
        need_tty("--output")
        raw_output = input("Output file name [%s]: " % default_name).strip() or default_name
    out_path = resolve_output(raw_output, default_name, args.force)
    if out_path == src:
        die("the output would overwrite the input; pick another name")

    pdf = open_pdf(src, password, password_from_flag=args.password is not None)
    try:
        save(pdf, out_path, new_password, owner_password, allow)
    except Exception as exc:
        die("failed to write %s: %s" % (out_path, exc))
    finally:
        pdf.close()

    action = {"remove": "Password removed", "change": "Password changed",
              "encrypt": "Password added"}[mode]
    print("%s -> %s" % (action, out_path))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit("\naborted")
