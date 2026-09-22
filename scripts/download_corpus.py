"""data/corpus_manifest.csv 의 논문 PDF를 data/raw/papers/ 로 받는다. (PDF는 커밋 금지)"""
import csv
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
for row in csv.DictReader(open(ROOT / "data/corpus_manifest.csv", encoding="utf-8")):
    out = ROOT / "data/raw/papers" / f"{row['doc_id']}.pdf"
    out.parent.mkdir(parents=True, exist_ok=True)
    if not out.exists():
        out.write_bytes(httpx.get(row["pdf_url"], follow_redirects=True, timeout=60).content)
    print("ok", out.name)
