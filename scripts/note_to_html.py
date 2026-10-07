"""Obsidian-flavoured note (.md) -> ONE self-contained, readable HTML file.

What it does, in order:
  1. strips the YAML frontmatter
  2. inlines every ![[path|width]] image as a base64 data: URI; a missing image
     becomes a visible `[缺圖 path]` marker and is counted in a stderr warning
  3. wikilinks [[a|b]] / [[a]] -> plain text
  4. ==x== -> <mark>x</mark>
  5. Obsidian callouts (`> [!type] title` + `>` body lines) -> <div class="co type">
  6. tabs -> 4 spaces, then pandoc (gfm -> standalone html5) with embedded CSS
     (Microsoft JhengHei stack, light theme, readable tables, max-width 900px)

Image lookup for `![[path|w]]`: <attach-base>/<path> first (default: the note's
folder), then <vault>/<path> (e.g. a `99-Attachment/...` path against the vault
root). --vault defaults to config.yaml `paths.vault_root` if that is set.

Usage:
    python note_to_html.py <note.md> [--out FILE] [--attach-base DIR] [--vault DIR]
      --out   default: <note>.html next to the note
Requires pandoc on PATH.
"""
import argparse, base64, html, mimetypes, os, re, subprocess, sys, tempfile

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

CSS = """body{font-family:'Microsoft JhengHei','Noto Sans TC','PingFang TC',sans-serif;max-width:900px;margin:30px auto;padding:0 20px;line-height:1.7;color:#222;background:#fff}
h1{border-bottom:2px solid #444;padding-bottom:4px}h2{border-bottom:1px solid #ccc;margin-top:1.6em}
table{border-collapse:collapse;margin:10px 0;font-size:.92em}td,th{border:1px solid #bbb;padding:4px 8px;vertical-align:top}th{background:#f0f0f0}
mark{background:#fff3a0}code{background:#eee;padding:0 4px;border-radius:3px}
.co{border-left:4px solid #888;background:#f7f7f7;padding:8px 14px;margin:12px 0;border-radius:4px}
.co.summary{border-color:#2a7;background:#eefaf3}.co.figure{border-color:#58c;background:#eef3fb}
.co.warning,.co.caution{border-color:#c80;background:#fff6e6}.co.danger,.co.bug{border-color:#c33;background:#fdeeee}
.ct{font-weight:bold;margin-bottom:4px}
img{max-width:100%;height:auto}"""

IMG_RE = re.compile(r"!\[\[([^\]|]+)\|?(\d+)?\]\]")
CALLOUT_RE = re.compile(r"^> \[!(\w+)\][+-]?\s*(.*)$")


def find_image(path, bases):
    rel = path.strip().replace("/", os.sep)
    for b in bases:
        if b:
            p = os.path.join(b, rel)
            if os.path.isfile(p):
                return p
    return None


def convert(text, bases):
    """Return (markdown_with_html, n_inlined, [missing_paths])."""
    text = text.replace("\r\n", "\n")
    text = re.sub(r"\A---\n.*?\n---\n", "", text, flags=re.S)  # frontmatter
    missing, inlined = [], [0]

    def img(m):
        path, width = m.group(1), m.group(2) or "600"
        p = find_image(path, bases)
        if not p:
            missing.append(path)
            return f"<em>[缺圖 {html.escape(path)}]</em>"
        mime = mimetypes.guess_type(p)[0] or "image/jpeg"
        with open(p, "rb") as fh:
            b64 = base64.b64encode(fh.read()).decode()
        inlined[0] += 1
        return (f'<img src="data:{mime};base64,{b64}" style="max-width:{width}px;width:100%;'
                f'border:1px solid #ccc;border-radius:4px;display:block;margin:6px 0">')

    text = IMG_RE.sub(img, text)
    text = re.sub(r"\[\[([^\]|]+)\|([^\]]+)\]\]", r"\2", text)  # wikilinks -> text
    text = re.sub(r"\[\[([^\]]+)\]\]", r"\1", text)
    text = re.sub(r"==(.+?)==", r"<mark>\1</mark>", text)

    lines, out, i = text.split("\n"), [], 0
    while i < len(lines):
        m = CALLOUT_RE.match(lines[i])
        if m:
            typ, title = m.group(1).lower(), m.group(2)
            body = []
            i += 1
            while i < len(lines) and lines[i].startswith(">"):
                body.append(lines[i][1:].lstrip(" "))
                i += 1
            out.append(f'<div class="co {typ}"><div class="ct">{html.escape(title, quote=False)}</div>\n\n'
                       + "\n".join(body) + "\n\n</div>")
            continue
        out.append(lines[i])
        i += 1
    return "\n".join(out).replace("\t", "    "), inlined[0], missing


def doc_title(text, note_path):
    body = re.sub(r"\A---\r?\n.*?\r?\n---\r?\n", "", text, flags=re.S)
    m = re.search(r"^#\s+(.+?)\s*$", body, flags=re.M)
    return m.group(1) if m else os.path.splitext(os.path.basename(note_path))[0]


def default_vault():
    try:
        from _common import load_config
        return ((load_config().get("paths") or {}).get("vault_root") or "").strip() or None
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser(description="Obsidian note -> single-file HTML (images inlined)")
    ap.add_argument("note")
    ap.add_argument("--out", default=None, help="output .html (default: next to the note)")
    ap.add_argument("--attach-base", default=None,
                    help="directory image paths are resolved against (default: the note's folder)")
    ap.add_argument("--vault", default=None,
                    help="vault root tried as a fallback for image paths (default: config paths.vault_root)")
    args = ap.parse_args()

    note = os.path.abspath(args.note)
    if not os.path.isfile(note):
        print(f"ERROR: {note} not found", file=sys.stderr)
        sys.exit(2)
    out_path = os.path.abspath(args.out) if args.out else os.path.splitext(note)[0] + ".html"
    bases = [os.path.abspath(args.attach_base) if args.attach_base else os.path.dirname(note),
             os.path.abspath(args.vault) if args.vault else default_vault()]

    with open(note, encoding="utf-8") as fh:
        raw = fh.read()
    md, n_ok, missing = convert(raw, bases)
    title = doc_title(raw, note)

    tmpdir = tempfile.mkdtemp(prefix="note2html_")
    md_path, css_path = os.path.join(tmpdir, "in.md"), os.path.join(tmpdir, "head.html")
    with open(md_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(md)
    with open(css_path, "w", encoding="utf-8") as fh:
        fh.write(f"<style>{CSS}</style>\n")
    cmd = ["pandoc", md_path, "-f", "gfm", "-t", "html5", "-s",
           "--metadata", f"pagetitle={title}", "-H", css_path, "-o", out_path]
    try:
        subprocess.run(cmd, check=True)
    except FileNotFoundError:
        print("ERROR: pandoc not found on PATH", file=sys.stderr)
        sys.exit(2)
    finally:
        for p in (md_path, css_path):
            try:
                os.remove(p)
            except OSError:
                pass
        try:
            os.rmdir(tmpdir)
        except OSError:
            pass

    if missing:
        print(f"WARNING: {len(missing)} image(s) not found (rendered as [缺圖 ...]): "
              + ", ".join(missing[:10]), file=sys.stderr)
    print(f"-> {out_path} ({os.path.getsize(out_path) // 1024} KB, "
          f"{n_ok} images inlined, {len(missing)} missing)")


if __name__ == "__main__":
    main()
