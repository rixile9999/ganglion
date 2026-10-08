#!/usr/bin/env python3
"""Render the formalization Markdown as standalone LaTeX using Pandoc.

Run from any directory. --check compares without changing repository files.
The generated .tex needs XeLaTeX (or Tectonic) and Noto Sans CJK KR to compile.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "docs/pipeline_formalization.md"
OUTPUT = SOURCE.with_suffix(".tex")

TEMPLATE = r"""% Generated from docs/pipeline_formalization.md.
% Regenerate with: python tools/build_formalization.py
% Compile with XeLaTeX or Tectonic; do not edit this generated file.
\documentclass[11pt,a4paper]{article}
\usepackage[margin=24mm]{geometry}
\usepackage{amsmath,amssymb}
\usepackage{fontspec}
\usepackage{xeCJK}
\setmainfont{lmroman10-regular.otf}[BoldFont=lmroman10-bold.otf,ItalicFont=lmroman10-italic.otf,BoldItalicFont=lmroman10-bolditalic.otf]
\setsansfont{lmsans10-regular.otf}[BoldFont=lmsans10-bold.otf]
\setmonofont{lmmono10-regular.otf}[Scale=MatchLowercase]
\setCJKmainfont{Noto Sans CJK KR}
\setCJKsansfont{Noto Sans CJK KR}
\setCJKmonofont{Noto Sans Mono CJK KR}
\usepackage{longtable,booktabs,array,calc}
\usepackage{etoolbox}
\makeatletter
\patchcmd\longtable{\par}{\if@noskipsec\mbox{}\fi\par}{}{}
\makeatother
\usepackage{fvextra}
\DefineVerbatimEnvironment{verbatim}{Verbatim}{breaklines=true,breakanywhere=true,fontsize=\small}
\usepackage{xurl}
\usepackage[unicode,colorlinks=true,linkcolor=blue,urlcolor=blue]{hyperref}
\usepackage{bookmark}
\setlength{\emergencystretch}{3em}
\setlength{\parindent}{0pt}
\setlength{\parskip}{0.55em}
\setcounter{secnumdepth}{-2}
\setcounter{tocdepth}{2}
\providecommand{\tightlist}{\setlength{\itemsep}{0pt}\setlength{\parskip}{0pt}}
\title{$title$}
\date{$date$}
\author{}
\begin{document}
\maketitle
\tableofcontents
\clearpage
$body$
\end{document}
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pandoc", default="pandoc", help="Pandoc executable")
    parser.add_argument("--check", action="store_true", help="Check without writing")
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="ganglion-formalization-") as tmp:
        template = Path(tmp) / "template.tex"
        template.write_text(TEMPLATE, encoding="utf-8")
        try:
            result = subprocess.run(
                [
                    args.pandoc, str(SOURCE),
                    "--from=markdown+tex_math_dollars", "--to=latex",
                    "--standalone", "--no-highlight", "--wrap=auto",
                    "--shift-heading-level-by=-1",
                    "--template", str(template),
                ],
                check=True, capture_output=True, text=True, encoding="utf-8",
            )
        except FileNotFoundError:
            parser.exit(2, "Pandoc not found; install it or pass --pandoc PATH.\n")
        except subprocess.CalledProcessError as exc:
            parser.exit(2, exc.stderr)

    rendered = result.stdout
    if args.check:
        if not OUTPUT.exists() or OUTPUT.read_text(encoding="utf-8") != rendered:
            print("LaTeX is out of sync; run tools/build_formalization.py.")
            return 1
        print("Markdown and LaTeX are in sync.")
        return 0
    OUTPUT.write_text(rendered, encoding="utf-8")
    print(f"Wrote {OUTPUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
