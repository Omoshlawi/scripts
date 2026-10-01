#!/usr/bin/env python3
"""Extract the audio track from a video, writing the result to out/.

By default the audio is copied as-is (fast, lossless) into a container that
matches its codec. Name an output with a different extension, or pass
--format, to convert instead.

  extractaudio.py [-o OUT] [--format FMT] [-b BITRATE] [-t N] [-f] VIDEO

Needs ffmpeg and ffprobe on PATH (macOS: brew install ffmpeg).
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile


INTERACTIVE = sys.stdin.isatty()


def die(message, code=1):
    sys.exit("error: %s" % message if code == 1 else message)


# Output format -> which source codecs it can hold without re-encoding, and the
# encoder to use when it can't. None marks a lossless format (no --bitrate).
FORMATS = {
    "mp3":  {"copy": {"mp3"}, "encoder": "libmp3lame", "bitrate": "192k"},
    "m4a":  {"copy": {"aac", "alac"}, "encoder": "aac", "bitrate": "192k"},
    "aac":  {"copy": {"aac"}, "encoder": "aac", "bitrate": "192k"},
    "opus": {"copy": {"opus"}, "encoder": "libopus", "bitrate": "128k"},
    "ogg":  {"copy": {"vorbis", "opus"}, "encoder": "libvorbis", "bitrate": "192k"},
    "wav":  {"copy": set(), "encoder": "pcm_s16le", "bitrate": None},
    "flac": {"copy": {"flac"}, "encoder": "flac", "bitrate": None},
    "mka":  {"copy": None, "encoder": "flac", "bitrate": None},  # None: holds anything
}

NATIVE_FORMAT = {"aac": "m4a", "alac": "m4a", "mp3": "mp3", "opus": "opus",
                 "vorbis": "ogg", "flac": "flac"}


def native_format(codec):
    if codec.startswith("pcm_"):
        return "wav"
    return NATIVE_FORMAT.get(codec, "mka")


def can_copy(codec, fmt):
    allowed = FORMATS[fmt]["copy"]
    if allowed is None:
        return True
    if fmt == "wav":
        return codec.startswith("pcm_")
    return codec in allowed


def require_tools():
    missing = [t for t in ("ffmpeg", "ffprobe") if shutil.which(t) is None]
    if missing:
        die("%s not found on PATH (macOS: brew install ffmpeg)" % " and ".join(missing))


def audio_streams(path):
    """Return the file's audio streams as dicts with codec_name and tags."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a",
             "-show_entries", "stream=index,codec_name,channels:stream_tags=language",
             "-of", "json", path],
            capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as exc:
        die("could not read %s: %s" % (path, exc.stderr.strip() or "ffprobe failed"))
    return json.loads(result.stdout).get("streams", [])


def describe(streams):
    lines = []
    for n, s in enumerate(streams):
        lang = s.get("tags", {}).get("language")
        lines.append("  %d: %s, %s ch%s" % (n, s.get("codec_name", "?"),
                                           s.get("channels", "?"),
                                           ", %s" % lang if lang else ""))
    return "\n".join(lines)


def resolve_output(raw, force):
    """Bare names land in out/; anything with a slash is used as given."""
    path = raw if os.sep in raw else os.path.join("out", raw)
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


def extract(src, out_path, track, codec_args):
    """Run ffmpeg into a temp file beside the output so a failure can't truncate."""
    suffix = os.path.splitext(out_path)[1]
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(out_path), suffix=suffix)
    os.close(fd)
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-stats", "-y",
           "-i", src, "-map", "0:a:%d" % track, "-vn", "-sn", "-dn",
           "-map_metadata", "0"] + codec_args + [tmp]
    try:
        if subprocess.run(cmd).returncode != 0:
            die("ffmpeg failed (see its output above); nothing written")
        os.replace(tmp, out_path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def main():
    parser = argparse.ArgumentParser(
        prog="extractaudio.py",
        description="Extract the audio track from a video. With no format given "
                    "the audio is copied losslessly into a matching container.",
        epilog="formats: %s" % ", ".join(sorted(FORMATS)),
    )
    parser.add_argument("input", help="the video to read")
    parser.add_argument("-o", "--output",
                        help="output file; a bare name goes into out/, and its "
                             "extension picks the format")
    parser.add_argument("--format", choices=sorted(FORMATS), metavar="FMT",
                        help="output format (default: whatever fits the source "
                             "codec without re-encoding)")
    parser.add_argument("-b", "--bitrate",
                        help="re-encode at this bitrate, e.g. 128k (lossy formats only)")
    parser.add_argument("-t", "--track", type=int, default=0, metavar="N",
                        help="which audio track to take, counting from 0 (default 0)")
    parser.add_argument("-f", "--force", action="store_true",
                        help="overwrite the output file if it exists")
    args = parser.parse_args()

    require_tools()

    src = os.path.abspath(args.input)
    if not os.path.isfile(src):
        die("no such file: %s" % args.input)

    streams = audio_streams(src)
    if not streams:
        die("%s has no audio track" % args.input)
    if not 0 <= args.track < len(streams):
        die("there is no audio track %d; %s has:\n%s"
            % (args.track, args.input, describe(streams)))
    codec = streams[args.track].get("codec_name", "")

    # Format: --format, else the -o extension, else whatever the codec fits.
    fmt = args.format
    if args.output:
        ext = os.path.splitext(args.output)[1].lower().lstrip(".")
        if ext:
            if ext not in FORMATS:
                die("don't know how to write .%s; use one of: %s"
                    % (ext, ", ".join(sorted(FORMATS))))
            if fmt and fmt != ext:
                die("--format %s contradicts the output name %s" % (fmt, args.output))
            fmt = ext
    if fmt is None:
        fmt = native_format(codec)

    spec = FORMATS[fmt]
    if args.bitrate and spec["bitrate"] is None:
        die("--bitrate makes no sense for %s; pick a lossy format such as mp3"
            % fmt)

    if can_copy(codec, fmt) and not args.bitrate:
        codec_args = ["-c:a", "copy"]
        action = "Copied %s audio" % codec
    else:
        codec_args = ["-c:a", spec["encoder"]]
        if spec["bitrate"]:
            codec_args += ["-b:a", args.bitrate or spec["bitrate"]]
        action = "Converted %s audio to %s" % (codec, fmt)

    stem = os.path.splitext(os.path.basename(src))[0]
    raw_output = args.output or "%s.%s" % (stem, fmt)
    if not os.path.splitext(raw_output)[1]:
        raw_output += "." + fmt
    out_path = resolve_output(raw_output, args.force)
    if out_path == src:
        die("the output would overwrite the input; pick another name")

    extract(src, out_path, args.track, codec_args)
    print("%s -> %s" % (action, out_path))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit("\naborted")
