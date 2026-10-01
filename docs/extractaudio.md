# extractaudio.py

Pull the audio track out of a video. By default it is copied as-is (fast, no quality
loss) into a container that matches its codec; the file lands in `out/`.

```sh
./extractaudio.py talk.mp4                      # -> out/talk.m4a (lossless copy)
./extractaudio.py talk.mp4 -o talk.mp3          # convert to mp3
./extractaudio.py talk.mkv --format opus -b 96k -t 1   # 2nd audio track, as 96k opus
```

Needs `ffmpeg` and `ffprobe` on `PATH` (`brew install ffmpeg`). No Python packages.

## Options

| Flag | Meaning |
| --- | --- |
| `input` | The video to read. |
| `-o`, `--output FILE` | Output file. A bare name goes into `out/`; anything with a slash is used as given. Its extension picks the format; with no extension one is added. Default: `out/<video name>.<format>`. |
| `--format FMT` | `mp3`, `m4a`, `aac`, `opus`, `ogg`, `wav`, `flac`, or `mka`. Default: whatever holds the source codec without re-encoding. Must agree with `-o`'s extension if both are given. |
| `-b`, `--bitrate RATE` | Re-encode at this bitrate, e.g. `128k`. Lossy formats only; forces a re-encode even when a copy would be possible. |
| `-t`, `--track N` | Which audio track to take, counting from 0. Default `0`. |
| `-f`, `--force` | Overwrite the output if it exists. |
| `-h`, `--help` | Usage. |

## Output

With no format chosen, the source codec decides the container:

| Source codec | Output |
| --- | --- |
| aac, alac | `.m4a` |
| mp3 | `.mp3` |
| opus | `.opus` |
| vorbis | `.ogg` |
| flac | `.flac` |
| pcm_* | `.wav` |
| anything else | `.mka` (Matroska audio holds any codec) |

When a format is chosen, the audio is still copied if the codec already fits that
container; otherwise it is converted (mp3 / aac / opus / vorbis default to
192k / 192k / 128k / 192k; wav is 16-bit PCM). The last line says which happened:

```
Copied aac audio -> /Users/omosh/projects/scripts/out/clip.m4a
Converted aac audio to mp3 -> /Users/omosh/projects/scripts/out/xtest.mp3
```

If the output exists, the script asks before overwriting on a terminal and refuses
otherwise (use `--force`). ffmpeg writes to a temp file first, so a failed run never
leaves a half-written output. The input is never overwritten.

## Notes and caveats

- Converting lossy to lossy (e.g. aac → mp3) loses a little quality; prefer the
  default copy when the player supports it.
- Container metadata (title, etc.) is carried over; video, subtitle and
  data streams are dropped.

## Exit codes

| Code | Meaning |
| --- | --- |
| 0 | Success. |
| 1 | No ffmpeg, unreadable input, no audio / bad track, output exists, or ffmpeg failed. |
| 2 | Bad usage (argparse). |

## Troubleshooting

**`error: ... has no audio track`** — the video is silent; there is nothing to extract.

**`error: there is no audio track N`** — the message lists the tracks the file has;
pick one of those numbers with `-t`.

**`Unknown encoder 'libmp3lame'`** (or `libopus`/`libvorbis`) — your ffmpeg build
lacks that encoder. Homebrew's `ffmpeg` includes them; or pick another format.
