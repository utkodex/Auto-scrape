"""Generate the one-page fake resume used for every test fill.

Usage: python scripts/make_dummy_resume.py
Writes profiles/dummy_resume.pdf from profiles/dummy.yaml.
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

REPO = Path(__file__).resolve().parent.parent
PROFILE = REPO / "profiles" / "dummy.yaml"
OUT = REPO / "profiles" / "dummy_resume.pdf"


def main() -> int:
    data = yaml.safe_load(PROFILE.read_text(encoding="utf-8"))
    c = canvas.Canvas(str(OUT), pagesize=A4)
    width, height = A4
    y = height - 72
    c.setFont("Helvetica-Bold", 18)
    c.drawString(72, y, f"{data['first_name']} {data['last_name']} - dummy resume")
    y -= 28
    c.setFont("Helvetica", 11)
    lines = [
        "This document is a placeholder generated for automated form tests.",
        f"Email: {data['email']}   Phone: {data['phone']}",
        f"Location: {data['city']}, {data['country']}",
        f"Title: {data['current_title']}   Experience: {data['total_experience_years']} years",
        f"Education: {data['highest_degree']} ({data['graduation_year']})",
        "Skills: " + ", ".join(data["skills"]),
        f"Profile: {data['linkedin']}",
    ]
    for line in lines:
        c.drawString(72, y, line)
        y -= 18
    c.showPage()
    c.save()
    print(f"wrote {OUT} ({OUT.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
