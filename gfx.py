#!/usr/bin/env python3
"""HTML template -> transparent 1920x1080 PNG via headless chromium."""
import hashlib, html, os, re, shutil, subprocess
from pathlib import Path

ROOT = Path(__file__).parent
TPL = ROOT / "templates"


def chrome():
    """Chromium/Chrome binary: VIDPIPE_CHROME env wins, then a name search."""
    if c := os.environ.get("VIDPIPE_CHROME"):
        return c
    for name in ("chromium", "chromium-browser", "google-chrome-stable",
                 "google-chrome", "chrome", "brave", "msedge"):
        if p := shutil.which(name):
            return p
    raise SystemExit("vidpipe: no chromium/chrome on PATH — install it or set VIDPIPE_CHROME")


def _fill(value):
    """Lists become <li> runs; everything else is escaped text (allow <mark>/<b>)."""
    if isinstance(value, (list, tuple)):
        return "".join(f"<li>{_inline(v)}</li>" for v in value)
    return _inline(value)


INLINE_OK = ("mark", "b", "i", "em", "strong", "br", "span")


def _inline(s):
    """Escape everything, then re-allow a fixed set of inline formatting tags."""
    s = html.escape(str(s))
    return re.sub(r"&lt;(/?)(" + "|".join(INLINE_OK) + r")( class=[a-z]+)?\s*/?&gt;",
                  lambda m: f"<{m.group(1)}{m.group(2)}{m.group(3) or ''}>", s)


def render(template, vars, out_dir, accent=None, w=1920, h=1080):
    src = (TPL / f"{template}.html").read_text().replace("/*CSS*/", (TPL / "base.css").read_text())
    for k, v in vars.items():
        src = src.replace("{{%s}}" % k, _fill(v))
    src = re.sub(r"\{\{\w+\}\}", "", src)          # unused placeholders
    if accent:
        src += f"<style>:root{{--accent:{accent}}}</style>"
    src = f"<meta charset=utf-8>{src}"

    out_dir = Path(out_dir); out_dir.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha1(f"{src}{w}{h}".encode()).hexdigest()[:16]
    png = out_dir / f"{template}-{key}.png"
    if png.exists():
        return png

    tmp = out_dir / f".{key}.html"
    tmp.write_text(src)
    subprocess.run([chrome(), "--headless=new", "--disable-gpu", "--no-sandbox",
                    "--hide-scrollbars", "--force-device-scale-factor=1",
                    f"--window-size={w},{h}", "--default-background-color=00000000",
                    "--virtual-time-budget=1200", f"--screenshot={png}", tmp.as_uri()],
                   check=True, capture_output=True)
    tmp.unlink()
    return png


if __name__ == "__main__":
    import sys
    p = render("lower_third", {"kicker": "privacy", "title": "GrapheneOS",
                               "sub": "hardened AOSP with verified boot"}, "/tmp/gfxtest")
    print("gfx.py selftest ok" if "--selftest" in sys.argv else p)
