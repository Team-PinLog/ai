"""`docs/troubleshooting` · `docs/implements` 의 색인이 실물과 어긋나는지 검사한다.

2026-07-31 하루에 같은 사고가 다섯 번 났다 — T43 중복, 색인 행 중복, 파일 표 미갱신,
T53 중복, 하네스 소실. **대응이 다섯 번 다 「문서에 문장 추가」였고, 네 번째는 첫
번째의 대응을 따랐는데도 났다.** 사람이 성실하지 않아서가 아니라, 번호를 세는 시점과
병합 시점이 어긋나는 것을 사람이 알 방법이 없기 때문이다. `.claude/README.md` 가 이미
적어 둔 원리다 — *"차이는 성실성이 아니라 강제 장치였다."*

이 스크립트는 규칙을 더하지 않는다. **이미 있는 규칙 중 기계가 셀 수 있는 둘을 사람
몫에서 뺀다.**

    ① 번호 중복    전수 표에서 T## · I## 이 같은 번호를 두 번 쓰면 실패
    ③ 고아 · 누락  파일 표가 가리키는 파일이 없거나, 있는 파일이 파일 표에 없으면 실패
    ④ 표 누락      전수 표가 가리키는 문서가 파일 표에 없으면 실패

번호가 **연속인지는 검사하지 않는다.** `T9`·`T10` 은 백엔드 아티팩트라 `back` 레포에
있고, 결번은 정상 상태다. 결번을 오류로 만들면 이 검사가 없는 규칙을 만들어 낸다.

**두 표(파일 표 · 전수 표)가 서로 일치하는지는 검사하지 않는다 — 누락만 본다(④).**
표가 둘로 나뉜 것 자체가 07-31 사고 `3`(파일 표 미갱신)의 원인이므로, 설명 문구나 번호
표기까지 맞추라고 하면 문제를 규칙으로 승격시키는 셈이 된다. 누락은 다르다 — 한쪽에만
적힌 문서는 사고이지 편집 재량이 아니다(07-31 사고 `5`).

**④ 는 한 방향뿐이다(전수 → 파일).** 반대 방향(파일 표에 있는데 전수 표가 안 가리킨다)
은 검사할 수 없다 — `docs/troubleshooting` 의 전수 표는 **설계상 문서를 가리키지 않고**
(T↔문서 매핑은 파일 표의 `(T16~T18)` 표기가 유일한 출처다) 그 방향을 켜면 정상 문서
15건이 전부 위반으로 나온다. `docs/implements` 도 문서 없는 산출(`docs#2`·로컬 메모리)이
전수 표에 섞여 있어 7건이 걸린다. 근거는 `docs/implements/2026-07-31-docs-index-check.md`.

**구조가 해소되면 ④ 는 불필요하다.** 표를 하나로 줄이거나 파일 표에 번호 컬럼을 두어
매핑을 한 곳에 모으면 「두 곳에 같은 사실을 적는다」가 사라지고, 그때 이 검사를 지운다.
남겨 두면 나중에 왜 있는지 모르는 검사가 된다.

로컬과 CI 가 같은 코드를 쓰도록 Python 으로 둔다(셸이면 CI 전용이 된다).
`tests/test_docs_index.py` 가 같은 함수를 부르므로 `pytest` 만 돌려도 신호가 나온다.

    python tools/check_docs_index.py

색인을 **읽을 수 없는 것도 실패**다. 섹션 제목을 바꿨는데 검사가 조용히 0건을 세면
게이트는 이름만 남는다 — "확인하지 못했다"는 통과가 아니다.
"""
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

INDEX_FILENAME = "README.md"

# 같은 디렉터리의 `.md` 를 가리키는 마크다운 링크. 하위 경로(`../spec/...`)와 외부
# URL 은 색인 항목이 아니므로 제외한다.
LOCAL_MD_LINK = re.compile(r"\[[^\]]*\]\(([^()\s]+\.md)\)")


@dataclass(frozen=True)
class IndexSpec:
    """색인 하나의 구조. 제목이 바뀌면 여기를 함께 고쳐야 검사가 계속 돈다."""

    label: str
    directory: str
    prefix: str
    file_section: str
    ledger_section: str


INDEXES = (
    IndexSpec(
        label="트러블슈팅",
        directory="docs/troubleshooting",
        prefix="T",
        file_section="개별 문서",
        ledger_section="문제 해결 — 전수 (AI 소유)",
    ),
    IndexSpec(
        label="구현 리포트",
        directory="docs/implements",
        prefix="I",
        file_section="개별 리포트",
        ledger_section="구현·산출 — 전수 (AI 소유)",
    ),
)


class DocsIndexError(Exception):
    """색인을 검사할 수 없다(파일 부재·섹션 제목 변경 등)."""


@dataclass(frozen=True)
class Finding:
    """위반 하나. `remedy` 는 「무엇을 어떻게 고치는가」까지 말해야 한다."""

    label: str
    kind: str
    detail: str
    remedy: str

    def render(self) -> list[str]:
        lines = [f"::error::[{self.label}] {self.kind} — {self.detail}"]
        lines.extend(f"    {line}" for line in self.remedy.splitlines())
        return lines


def find_section(lines: list[str], heading: str, source: str) -> tuple[int, list[str]]:
    """`## {heading}` 다음부터 다음 `## ` 앞까지를 (0-기준 시작 줄, 본문)으로 돌려준다."""
    target = f"## {heading}"
    start = None
    for index, line in enumerate(lines):
        if line.rstrip() == target:
            start = index + 1
            break
    if start is None:
        raise DocsIndexError(
            f"{source} 에서 「{heading}」 섹션을 찾지 못했다 — "
            "제목을 바꿨으면 tools/check_docs_index.py 의 INDEXES 를 함께 고쳐야 한다"
        )

    end = len(lines)
    for index in range(start, len(lines)):
        if lines[index].startswith("## "):
            end = index
            break
    return start, lines[start:end]


def first_cell(line: str) -> str | None:
    """마크다운 표 행의 첫 칸. 표 행이 아니면 None."""
    stripped = line.strip()
    if not stripped.startswith("|"):
        return None
    return stripped.strip("|").split("|")[0].strip()


def parse_ledger(
    lines: list[str], offset: int, prefix: str
) -> tuple[dict[int, list[int]], list[tuple[int, str]]]:
    """전수 표의 첫 칸에서 번호를 뽑는다. (번호 -> 줄번호들, 형식 위반 행)"""
    pattern = re.compile(rf"{prefix}(\d+)")
    numbers: dict[int, list[int]] = {}
    malformed: list[tuple[int, str]] = []

    for index, line in enumerate(lines):
        cell = first_cell(line)
        if cell is None or not cell:
            continue
        if set(cell) <= {"-", ":"}:  # 구분선
            continue
        if cell == prefix:  # 헤더
            continue
        match = pattern.fullmatch(cell)
        if match is None:
            malformed.append((offset + index + 1, cell))
            continue
        numbers.setdefault(int(match.group(1)), []).append(offset + index + 1)

    return numbers, malformed


def parse_table_links(lines: list[str], offset: int) -> dict[str, list[int]]:
    """표 행이 가리키는 같은 디렉터리 문서. (파일명 -> 줄번호들)

    파일 표와 전수 표 양쪽에 쓴다 — 전수 표는 대부분의 행이 문서를 가리키지 않지만
    (`docs#2`, 로컬 메모리, `spec/`) 가리키는 행은 파일 표와 같은 형태다.
    """
    entries: dict[str, list[int]] = {}
    for index, line in enumerate(lines):
        if first_cell(line) is None:
            continue
        for target in LOCAL_MD_LINK.findall(line):
            if "/" in target:
                continue
            entries.setdefault(target, []).append(offset + index + 1)
    return entries


@dataclass(frozen=True)
class IndexReport:
    """검사 결과 하나. `highest` 는 다음 번호를 고를 때 쓴다."""

    spec: IndexSpec
    findings: list[Finding]
    highest: int | None
    documents: int

    def summary(self) -> str:
        head = (
            f"{self.spec.label:<12} 문서 {self.documents}건"
            f" · 전수 {self.spec.prefix}1~{self.spec.prefix}{self.highest}"
            if self.highest is not None
            else f"{self.spec.label:<12} 문서 {self.documents}건 · 전수 표 비어 있음"
        )
        if self.highest is None:
            return head
        return f"{head} (다음 번호 {self.spec.prefix}{self.highest + 1})"


def check_index(root: Path, spec: IndexSpec) -> IndexReport:
    """색인 하나를 파일 시스템에 대조한다."""
    directory = root / spec.directory
    readme = directory / INDEX_FILENAME
    source = f"{spec.directory}/{INDEX_FILENAME}"

    if not readme.is_file():
        raise DocsIndexError(f"{source} 이 없다 — 색인 없이는 대조할 것이 없다")

    lines = readme.read_text(encoding="utf-8").splitlines()
    ledger_offset, ledger_lines = find_section(lines, spec.ledger_section, source)
    file_offset, file_lines = find_section(lines, spec.file_section, source)

    numbers, malformed = parse_ledger(ledger_lines, ledger_offset, spec.prefix)
    entries = parse_table_links(file_lines, file_offset)
    ledger_links = parse_table_links(ledger_lines, ledger_offset)

    findings: list[Finding] = []

    # ① 번호 중복. 07-31 사고 1(T43)·4(T53) 이 이것이다. 번호를 「분기 시점의 마지막
    #    다음」으로 잡으면 그 사이 병합된 병렬 티켓과 겹치는데, 전수 표는 정렬되어
    #    있지 않아 중복이 나란히 서지 않는다(T48·T60).
    for number, linenos in sorted(numbers.items()):
        if len(linenos) == 1:
            continue
        where = ", ".join(f"{source}:{lineno}" for lineno in linenos)
        findings.append(
            Finding(
                label=spec.label,
                kind="번호 중복",
                detail=f"{spec.prefix}{number} 이 전수 표에 {len(linenos)}번 있다 ({where})",
                remedy=(
                    f"같은 번호를 두 티켓이 잡았으면 뒤에 병합되는 쪽을 "
                    f"{spec.prefix}{max(numbers) + 1} 이후로 옮긴다. 본문·파일 표의 "
                    f"「({spec.prefix}nn)」 표기도 함께 바꾼다.\n"
                    "같은 문서가 두 판으로 남은 것이면(merge=union 이 갱신된 행의 낡은 "
                    "판을 되살린다, T56) 낡은 행을 지운다.\n"
                    "번호는 origin/dev 를 병합한 **뒤에** 확정한다 — 세는 시점이 병합보다 "
                    "앞이면 그 사이 들어온 행이 보이지 않는다(T60)."
                ),
            )
        )

    for lineno, cell in malformed:
        findings.append(
            Finding(
                label=spec.label,
                kind="전수 표 형식",
                detail=f"첫 칸이 `{spec.prefix}nn` 형식이 아니다: {cell!r} ({source}:{lineno})",
                remedy=(
                    f"전수 표의 첫 칸은 번호 하나여야 한다(`{spec.prefix}61` 같은 형태). "
                    "여러 번호를 한 행에 묶으면 이 검사가 그 번호들을 세지 못한다.\n"
                    "표가 아닌 내용이면 「전수」 섹션 밖으로 옮긴다."
                ),
            )
        )

    # ② 파일 표에 같은 문서가 두 줄. 07-31 사고 2 가 이것이다 — 행을 **갱신**하면
    #    merge=union 이 낡은 판을 되살린다(추가만 할 때는 나지 않는다).
    for name, linenos in sorted(entries.items()):
        if len(linenos) == 1:
            continue
        where = ", ".join(f"{source}:{lineno}" for lineno in linenos)
        findings.append(
            Finding(
                label=spec.label,
                kind="색인 행 중복",
                detail=f"{name} 이 파일 표에 {len(linenos)}번 있다 ({where})",
                remedy=(
                    "두 줄을 비교해 최신 내용 한 줄만 남긴다 — merge=union 이 갱신된 "
                    "행의 낡은 판을 되살린 것이다(T56).\n"
                    "다음부터는 색인 행을 고쳤으면 병합 뒤에 확인한다."
                ),
            )
        )

    # ③ 누락 — 색인이 가리키는 파일이 없다.
    for name in sorted(entries):
        if (directory / name).is_file():
            continue
        where = ", ".join(f"{source}:{lineno}" for lineno in entries[name])
        findings.append(
            Finding(
                label=spec.label,
                kind="누락",
                detail=f"파일 표가 가리키는 {name} 이 {spec.directory}/ 에 없다 ({where})",
                remedy=(
                    "파일명을 바꿨으면 색인의 링크를 같이 고친다.\n"
                    "병합 과정에서 파일이 사라졌으면 되살린다 — 이 폴더의 보존 원칙상 "
                    "문서는 삭제하지 않고 상태 표기만 갱신한다."
                ),
            )
        )

    actual = {path.name for path in directory.glob("*.md") if path.name != INDEX_FILENAME}

    # ④ 표 누락 — 전수 표는 가리키는데 파일 표에 없다. 07-31 사고 5 가 이것이다
    #    (`-223` 의 리포트가 I33 에만 있었다). 한 방향뿐인 이유는 모듈 docstring 참조.
    #    구조가 해소되면 이 검사는 불필요하다.
    #
    #    같은 파일을 아래 ③ 고아가 또 잡지 않도록 여기서 처리한 것을 빼 둔다 — 한 문제에
    #    두 줄이 나오면 고칠 곳이 둘인 것처럼 읽힌다.
    ledger_only = sorted(set(ledger_links) - set(entries))
    for name in ledger_only:
        where = ", ".join(f"{source}:{lineno}" for lineno in ledger_links[name])
        exists = (directory / name).is_file()
        findings.append(
            Finding(
                label=spec.label,
                kind="표 누락",
                detail=(
                    f"전수 표가 가리키는 {name} 이 파일 표에 없다 ({where})"
                    + ("" if exists else f" — {spec.directory}/ 에 파일도 없다")
                ),
                remedy=(
                    (
                        f"{source} 의 「{spec.file_section}」 표에 "
                        f"`| [{name}]({name}) | ... |` 행을 추가한다.\n"
                        "전수 표에 행을 더할 때 파일 표를 잊는 것이 07-31 사고 3·5 다 — "
                        "두 표는 서로 다른 축이라 한쪽이 다른 쪽을 대신하지 못한다."
                    )
                    if exists
                    else (
                        "파일명을 바꿨으면 전수 표의 링크를 같이 고친다.\n"
                        "문서를 아직 안 썼으면 전수 표의 링크를 지우거나 문서를 만든다 — "
                        "가리키는 곳이 없는 링크는 색인이 아니다."
                    )
                ),
            )
        )

    # ③ 고아 — 파일이 있는데 색인에 없다. 07-31 사고 3 이 이것이다.
    for name in sorted(actual - set(entries) - set(ledger_only)):
        findings.append(
            Finding(
                label=spec.label,
                kind="고아",
                detail=f"{spec.directory}/{name} 이 파일 표에 없다",
                remedy=(
                    f"{source} 의 「{spec.file_section}」 표에 "
                    f"`| [{name}]({name}) | ... |` 행을 추가한다.\n"
                    "전수 표에만 링크를 걸어 두면 이 검사가 파일 표를 채운 것으로 보지 "
                    "않는다 — 파일 표가 「어떤 문서가 있는가」의 목록이다."
                ),
            )
        )

    return IndexReport(
        spec=spec,
        findings=findings,
        highest=max(numbers) if numbers else None,
        documents=len(actual),
    )


def check_repository(root: Path = ROOT) -> list[IndexReport]:
    """모든 색인을 검사한다. 읽지 못하면 DocsIndexError."""
    return [check_index(root, spec) for spec in INDEXES]


def main() -> int:
    # 콘솔이 cp949 면 이 스크립트의 한글·`—` 가 UnicodeEncodeError 로 죽는다(T28).
    # 호출자가 PYTHONIOENCODING 을 기억하게 두지 않는다.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="문서 색인 정합 검사 (번호 중복 · 고아/누락)")
    parser.add_argument(
        "--root",
        default=str(ROOT),
        help="레포 루트 (기본: 이 스크립트의 상위 디렉터리)",
    )
    arguments = parser.parse_args()

    try:
        reports = check_repository(Path(arguments.root))
    except DocsIndexError as error:
        print(f"::error::문서 색인 검사 불가 — {error}")
        return 1

    for report in reports:
        print(report.summary())

    findings = [finding for report in reports for finding in report.findings]
    if not findings:
        print("문서 색인 정합 통과")
        return 0

    print("")
    for finding in findings:
        for line in finding.render():
            print(line)
    print("")
    print(f"::error::문서 색인 위반 {len(findings)}건 — 위 항목을 고친 뒤 다시 돌린다.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
