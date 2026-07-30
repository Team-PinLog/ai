"""`PINLOG_TOKEN_LOG` JSONL 을 읽어 GMS 토큰·시간 사용량을 집계한다.

시딩 한 번의 비용을 숫자로 남기기 위한 도구다. 임베딩과 판정은 단가도 다르고
호출 패턴도 다르므로(임베딩은 배치 가능, 판정은 건당 1회) 따로 센다.

사용:
    python tools/demo_seed/token_report.py [경로]

경로를 생략하면 `PINLOG_TOKEN_LOG` 환경변수, 그것도 없으면
`.demo/token-usage.jsonl` 을 읽는다.
"""

from __future__ import annotations

import json
import os
import sys
from collections import defaultdict

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def main() -> int:
    path = (
        sys.argv[1]
        if len(sys.argv) > 1
        else os.environ.get("PINLOG_TOKEN_LOG", ".demo/token-usage.jsonl")
    )
    if not os.path.exists(path):
        print(f"토큰 로그가 없다: {path}")
        print("시딩 시 PINLOG_TOKEN_LOG 를 설정했는지 확인하라.")
        return 3

    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    if not rows:
        print("기록이 비어 있다.")
        return 3

    by = defaultdict(list)
    for r in rows:
        by[r["kind"]].append(r)

    print("=" * 72)
    print(f"GMS 토큰 사용량  ({path})")
    print("=" * 72)

    grand = 0
    for kind in ("embedding", "judge"):
        rs = by.get(kind, [])
        if not rs:
            continue
        n = len(rs)

        def s(field: str) -> int:
            return sum(r.get(field) or 0 for r in rs)

        total = s("total")
        grand += total
        print(f"\n[{kind}]  호출 {n}회")
        print(f"  total     {total:>8,}   건당 평균 {total / n:>8.1f}")
        if kind == "embedding":
            print(f"  prompt    {s('prompt'):>8,}")
        else:
            print(f"  prompt    {s('prompt'):>8,}   건당 {s('prompt') / n:>8.1f}")
            print(f"  output    {s('output'):>8,}   건당 {s('output') / n:>8.1f}")
            th = s("thoughts")
            note = "  (thinkingBudget=0 이라 0이어야 정상)" if th == 0 else "  ← 0이 아니다"
            print(f"  thoughts  {th:>8,}{note}")

        ts = sorted(r["at"] for r in rs if r.get("at"))
        if len(ts) > 1:
            span = ts[-1] - ts[0]
            print(f"  구간      {span / 60:>8.1f}분   호출 간격 평균 {span / (n - 1):>6.1f}초")

    print(f"\n{'─' * 72}")
    print(f"총 토큰 {grand:,}  ·  총 호출 {len(rows)}회")

    ts = sorted(r["at"] for r in rows if r.get("at"))
    if len(ts) > 1:
        print(f"전체 구간 {(ts[-1] - ts[0]) / 60:.1f}분")
    return 0


sys.exit(main())
