"""`labels.yaml` 을 손으로 채우기 위한 시트를 만든다. **판정은 사람이 한다.**

`matrix.json` 은 장소명까지만 담는다(커밋 대상이라 본문을 넣지 않는다). 그런데 「이
결과가 질의와 무관한가」는 장소명만으로 판정할 수 없다 — 「치킨버거 이스트사이드」의
본문에 「그네 공원 갔음」이 있어 공원 질의와 실제로 관련되는 식이다(`-191` §조건 B).

그래서 라벨을 붙일 때만 DB 에서 본문을 읽어 붙인다. **이 출력물은 커밋하지 않는다**
(`.search/` 는 `matrix.json` 만 예외로 둔다).

    python tools/search_cut/label_sheet.py            # .search/sheet.txt
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.core.config import get_settings  # noqa: E402
from app.core.db import Database  # noqa: E402

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

_BODIES = """
SELECT c.record_id, string_agg(c.body, ' / ' ORDER BY c.id) AS body
FROM core.context c
WHERE c.deleted_at IS NULL
GROUP BY c.record_id
"""


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--matrix", default=str(ROOT / ".search" / "matrix.json"))
    ap.add_argument("--out", default=str(ROOT / ".search" / "sheet.txt"))
    ap.add_argument("--body", type=int, default=70, help="본문 표시 길이")
    args = ap.parse_args()

    data = json.loads(Path(args.matrix).read_text(encoding="utf-8"))

    settings = get_settings()
    db = Database(settings.database_url)
    await db.connect()
    try:
        async with db.acquire() as conn:
            body = {r["record_id"]: (r["body"] or "") for r in await conn.fetch(_BODIES)}
    finally:
        await db.disconnect()

    lines: list[str] = []
    for q in data["queries"]:
        lines.append("")
        lines.append(f"# {q['query']}   (as={q['as']} · 기대 「{q['expect_name']}」)")
        for r in q["results"]:
            text = body.get(r["record_id"], "").replace("\n", " ")[: args.body]
            mark = "*" if r["is_expected"] else " "
            lines.append(
                f"  {mark} rec={r['record_id']} r{r['rank']:<2} {r['sim']:.4f} "
                f"{r['name'][:20]:<22} {text}"
            )
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines), flush=True)
    print(f"\n  → {out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
