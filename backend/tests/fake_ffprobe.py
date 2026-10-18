#!/usr/bin/env python3
"""A stand-in for ffprobe, for tests: reads nothing but this file's own kind
of tag. ``-show_entries format_tags -of json <file>`` prints the tags the
fake yt-dlp's --embed-metadata appended to the file (its FAKETAGS line), as
ffprobe prints a file's format tags; {} without. Anything else fails (exit
1), as ffprobe does on a file it cannot read: what asks for a video's size
or length then does without."""
import json
import sys

TAGS = b"\nFAKETAGS "


def main(argv):
    if "format_tags" not in argv or not argv:
        print(f"{argv[-1] if argv else '?'}: Invalid data found when processing input", file=sys.stderr)
        return 1
    try:
        with open(argv[-1], "rb") as f:
            data = f.read()
    except OSError as e:
        print(f"{argv[-1]}: {e.strerror}", file=sys.stderr)
        return 1
    at = data.rfind(TAGS)
    tags = json.loads(data[at + len(TAGS):].decode()) if at >= 0 else {}
    print(json.dumps({"format": {"tags": tags}} if tags else {"format": {}}))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
