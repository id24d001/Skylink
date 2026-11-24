"""Render report/decision_memo.md to report/decision_memo.pdf (A4) and report its page count.

    python src/m7_decision/memo_pdf.py            # needs pandoc and a Chrome/Chromium binary

Not part of run_all.py (it needs tools outside requirements.txt). The brief asks for a two-page
memo, so the script fails if the PDF is longer than two pages.
"""
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MD, PDF = ROOT / "report" / "decision_memo.md", ROOT / "report" / "decision_memo.pdf"

CSS = """
@page { size: A4; margin: 13mm 15mm 12mm 15mm; }
body { font-family: 'DejaVu Sans', Arial, sans-serif; font-size: 8.6pt; line-height: 1.32; color: #111; }
header h1.title { font-size: 14pt; margin: 0 0 1mm 0; }
header p.subtitle { font-size: 9.5pt; margin: 0 0 2mm 0; color: #444; }
h2 { font-size: 10.2pt; margin: 3.2mm 0 1mm 0; border-bottom: 0.4pt solid #999; padding-bottom: 0.5mm; }
p { margin: 1mm 0; } ul, ol { margin: 0.6mm 0 0.6mm 0; padding-left: 5mm; } li { margin: 0.3mm 0; }
table { border-collapse: collapse; width: 100%; margin: 1.2mm 0; font-size: 8.2pt; }
th, td { border: 0.4pt solid #bbb; padding: 0.8mm 1.6mm; text-align: left; vertical-align: top; }
th { background: #eef2ee; }
em { color: #444; }
"""


def main():
    with tempfile.TemporaryDirectory() as d:
        html = Path(d) / "memo.html"
        css = Path(d) / "memo.css"
        css.write_text(CSS)
        subprocess.run(["pandoc", str(MD), "-s", "-o", str(html), "--css", str(css),
                        "--metadata", "pagetitle=SkyLink decision memo"], check=True)
        chrome = shutil.which("chromium") or shutil.which("google-chrome") or "/opt/pw-browsers/chromium"
        subprocess.run([chrome, "--headless", "--no-sandbox", "--disable-gpu", "--no-pdf-header-footer",
                        f"--print-to-pdf={PDF}", html.as_uri()], check=True, capture_output=True)
    n = len(re.findall(rb"/Type\s*/Page[^s]", PDF.read_bytes()))
    print(f"{PDF.relative_to(ROOT)}: {n} page(s)")
    if n > 2:
        sys.exit("memo is longer than two pages")


if __name__ == "__main__":
    main()
