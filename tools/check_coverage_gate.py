"""`app` 의 line·branch coverage 가 각각 임계값 이상인지 검사한다.

`pytest --cov-fail-under` 를 쓰지 않는 이유가 이 스크립트의 존재 이유다. `--cov-branch`
를 켜면 coverage.py 의 `percent_covered` 는 **statement 와 branch 를 합산한 하나의
비율**이 된다. 그 값이 80 을 넘어도 branch 만 60% 인 상태가 통과한다 — 합산 비율은
statement 수가 branch 수보다 훨씬 많다는 사실에 가려진다. `S15P11A705-110` 의 완료
조건은 *"line 과 branch 각각 80% 이상"* 이므로 둘을 따로 본다.

    pytest --cov=app --cov-branch --cov-report=json:coverage.json
    python tools/check_coverage_gate.py

`--cov-branch` 없이 만든 리포트는 `num_branches == 0` 이라 모든 분기 검사가 공짜로
통과한다. 그 상태를 성공으로 처리하면 게이트가 아무것도 강제하지 않으므로 오류로
끊는다 — "측정하지 못했다"는 통과가 아니다.

임계값은 아래 상수 하나뿐이고 CLI 로 덮을 수 없다. 덮을 수 있게 두면 CI 가 조용히
낮은 값을 넘겨 게이트를 무력화할 수 있다. 기준선을 바꿀 때는 이 파일을 고치고 그
근거를 PR 에 남긴다.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

DEFAULT_COVERAGE_JSON = ROOT / "coverage.json"

# S15P11A705-110 의 완료 조건. 두 값은 독립이며 하나라도 미달이면 실패다.
LINE_MIN = 80.0
BRANCH_MIN = 80.0

NO_BRANCH_DATA = (
    "branch 측정치가 없다 — `--cov-branch` 없이 만든 리포트다. "
    "이 상태를 통과로 두면 branch 게이트가 아무것도 강제하지 않는다"
)


class CoverageGateError(Exception):
    """게이트를 판정할 수 없다(리포트 부재·형식 위반·측정 누락)."""


@dataclass(frozen=True)
class Metric:
    name: str
    covered: int
    total: int
    minimum: float

    @property
    def percent(self) -> float:
        return 100.0 * self.covered / self.total

    @property
    def ok(self) -> bool:
        return self.percent >= self.minimum

    def render(self) -> str:
        verdict = "ok" if self.ok else "FAILED"
        return (
            f"{self.name:<6} {self.percent:6.2f}%  "
            f"({self.covered}/{self.total})  >= {self.minimum:.2f}%  {verdict}"
        )


def evaluate(
    totals: dict, line_min: float = LINE_MIN, branch_min: float = BRANCH_MIN
) -> list[Metric]:
    """coverage.json 의 `totals` 를 두 개의 독립 지표로 환원한다.

    반환 순서는 line, branch 로 고정한다. 판정은 호출자가 `all(m.ok ...)` 로 한다.
    """
    # `--cov-branch` 없이 만든 리포트에는 branch 키 자체가 없다. 형식 오류로 뭉뚱그리면
    # 원인(측정 플래그 누락)이 메시지에서 사라진다.
    if "num_branches" not in totals or "covered_branches" not in totals:
        raise CoverageGateError(NO_BRANCH_DATA)

    try:
        num_statements = int(totals["num_statements"])
        covered_lines = int(totals["covered_lines"])
        num_branches = int(totals["num_branches"])
        covered_branches = int(totals["covered_branches"])
    except (KeyError, TypeError, ValueError) as error:
        raise CoverageGateError(f"coverage.json 의 totals 형식을 해석할 수 없다: {error}") from error

    if num_statements <= 0:
        raise CoverageGateError(
            "측정된 statement 가 0 이다 — --cov=app 이 빠졌거나 리포트가 비어 있다"
        )
    if num_branches <= 0:
        raise CoverageGateError(NO_BRANCH_DATA)

    return [
        Metric("line", covered_lines, num_statements, line_min),
        Metric("branch", covered_branches, num_branches, branch_min),
    ]


def load_totals(path: Path) -> dict:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise CoverageGateError(
            f"coverage.json 이 없다 ({path}) — "
            "`pytest --cov-report=json:coverage.json` 이 먼저 돌아야 한다"
        ) from error
    except (OSError, json.JSONDecodeError) as error:
        raise CoverageGateError(f"coverage.json 을 읽을 수 없다 ({path}): {error}") from error

    totals = document.get("totals")
    if not isinstance(totals, dict):
        raise CoverageGateError(f"coverage.json 에 totals 매핑이 없다 ({path})")
    return totals


def main() -> int:
    parser = argparse.ArgumentParser(description="app line·branch coverage 게이트")
    parser.add_argument(
        "--coverage-json",
        default=str(DEFAULT_COVERAGE_JSON),
        help="coverage.py JSON 리포트 경로 (기본: 레포 루트의 coverage.json)",
    )
    arguments = parser.parse_args()

    try:
        metrics = evaluate(load_totals(Path(arguments.coverage_json)))
    except CoverageGateError as error:
        print(f"::error::coverage 게이트 판정 불가 — {error}")
        return 1

    for metric in metrics:
        print(metric.render())

    failed = [metric for metric in metrics if not metric.ok]
    if failed:
        print(
            "::error::coverage 게이트 미달 — "
            + ", ".join(
                f"{m.name} {m.percent:.2f}% < {m.minimum:.2f}%" for m in failed
            )
            + ". 제외(pragma·omit)로 수치를 맞추지 말고 테스트를 보강한다 "
            "(CONTRIBUTING.md 검증 절)."
        )
        return 1

    print("coverage 게이트 통과")
    return 0


if __name__ == "__main__":
    sys.exit(main())
