#!/usr/bin/env python3
"""
md2pdf.py — render a Markdown file to a print-styled PDF via headless Chrome.

Lives in the repo because the ARCHITECTURE and README PDFs are committed
artefacts, and a generator that only exists on one machine means they quietly
go stale the first time someone else edits the source.

Chrome is used rather than a Python PDF library because it already renders
GitHub-flavoured tables, nested lists and code blocks correctly, and those are
most of what these documents are made of.

Usage:
    python3 tools/md2pdf.py ARCHITECTURE.md            # -> ARCHITECTURE.pdf
    python3 tools/md2pdf.py README.md README.pdf
    python3 tools/md2pdf.py --all                      # both committed docs

Requires: `pip install markdown`, and Google Chrome.
"""
import argparse
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
COMMITTED_DOCS = ['ARCHITECTURE.md', 'README.md']

CHROME_CANDIDATES = [
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
    '/usr/bin/google-chrome',
    '/usr/bin/chromium-browser',
    '/usr/bin/chromium',
]

CSS = """
@page { size: A4; margin: 16mm 14mm 18mm 14mm; }
* { box-sizing: border-box; }
body {
  font-family: -apple-system, "Helvetica Neue", Helvetica, Arial, sans-serif;
  font-size: 10.2pt; line-height: 1.52; color: #1a1d21; margin: 0;
  -webkit-print-color-adjust: exact; print-color-adjust: exact;
}
h1 {
  font-size: 21pt; letter-spacing: -0.02em; margin: 0 0 .15em;
  color: #0b1220; border-bottom: 3px solid #2563EB; padding-bottom: .3em;
}
h2 {
  font-size: 14.5pt; margin: 1.9em 0 .6em; color: #0b1220;
  border-bottom: 1px solid #e2e6ea; padding-bottom: .22em; page-break-after: avoid;
}
h3 { font-size: 11.6pt; margin: 1.35em 0 .45em; color: #1e293b; page-break-after: avoid; }
h4 { font-size: 10.6pt; margin: 1.2em 0 .4em; color: #334155; page-break-after: avoid; }
h1 + p { color: #475569; font-size: 11pt; }
p, li { orphans: 3; widows: 3; }
ul, ol { padding-left: 1.25em; }
li { margin: .22em 0; }
strong { color: #0b1220; }
hr { border: 0; border-top: 1px solid #e2e6ea; margin: 1.8em 0; }
code {
  font-family: "SF Mono", Menlo, Consolas, monospace; font-size: 8.9pt;
  background: #f1f5f9; padding: .1em .34em; border-radius: 3px; color: #0f172a;
}
pre {
  background: #f8fafc; border: 1px solid #e2e6ea; border-left: 3px solid #2563EB;
  border-radius: 4px; padding: .75em .9em; overflow-x: auto;
  page-break-inside: avoid; margin: .9em 0;
}
pre code { background: none; padding: 0; font-size: 7.9pt; line-height: 1.42;
           white-space: pre; color: #0f172a; }
table { border-collapse: collapse; width: 100%; margin: .9em 0; font-size: 9.1pt;
        page-break-inside: avoid; }
th { background: #f1f5f9; text-align: left; font-weight: 600; color: #0b1220;
     border-bottom: 2px solid #cbd5e1; padding: .46em .6em; }
td { border-bottom: 1px solid #eef2f6; padding: .42em .6em; vertical-align: top; }
tr:nth-child(even) td { background: #fbfcfd; }
blockquote { border-left: 3px solid #cbd5e1; margin: 1em 0; padding: .1em 1em; color: #475569; }
a { color: #2563EB; text-decoration: none; }
"""


def find_chrome():
    for c in CHROME_CANDIDATES:
        if Path(c).exists():
            return c
    found = shutil.which('google-chrome') or shutil.which('chromium')
    if found:
        return found
    sys.exit("Chrome not found — install it, or add its path to CHROME_CANDIDATES.")


def render(src: Path, out: Path):
    try:
        import markdown
    except ImportError:
        sys.exit("Missing dependency: pip install markdown")

    # Chrome is handed a file:// URI, and Path.as_uri() refuses relative paths.
    # Resolving here rather than at the call sites means every entry point —
    # --all, one argument, or two — gets the same treatment.
    src = src.resolve()
    out = out.resolve()

    if not src.exists():
        sys.exit(f"No such file: {src}")

    body = markdown.markdown(
        src.read_text(),
        extensions=['tables', 'fenced_code', 'toc', 'attr_list', 'sane_lists'],
    )
    html = out.with_suffix('.tmp.html')
    html.write_text(
        f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
        f'<title>{src.stem}</title><style>{CSS}</style></head>'
        f'<body>{body}</body></html>'
    )
    try:
        subprocess.run([
            find_chrome(), '--headless', '--disable-gpu', '--no-sandbox',
            '--no-pdf-header-footer', '--run-all-compositor-stages-before-draw',
            '--virtual-time-budget=10000',
            f'--print-to-pdf={out}', html.as_uri(),
        ], check=True, capture_output=True)
    finally:
        html.unlink(missing_ok=True)

    size = out.stat().st_size
    print(f"  {src.name:20s} -> {out.name:20s} {size/1024:>6.0f} KB")
    # A near-empty PDF renders without error, so check the output is plausible
    # rather than trusting the exit code.
    if size < 20_000:
        sys.exit(f"{out.name} is suspiciously small — check the rendering.")


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[1])
    ap.add_argument('source', nargs='?', help='Markdown file to render')
    ap.add_argument('output', nargs='?', help='PDF path (default: alongside source)')
    ap.add_argument('--all', action='store_true',
                    help='Render every committed doc (%s)' % ', '.join(COMMITTED_DOCS))
    args = ap.parse_args()

    if args.all:
        for name in COMMITTED_DOCS:
            src = REPO / name
            render(src, src.with_suffix('.pdf'))
    elif args.source:
        src = Path(args.source)
        render(src, Path(args.output) if args.output else src.with_suffix('.pdf'))
    else:
        ap.error("give a Markdown file to render, or --all")
