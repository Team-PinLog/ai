"""문서 색인 정합 검사가 07-31 의 사고들을 실제로 잡는지 고정한다.

이 티켓의 전제는 *"규칙을 더하는 것이 아니라 기계로 옮긴다"* 이므로, 검사가 **잡는
것**과 **통과시키는 것**을 둘 다 못 박아야 한다. 특히 결번(T9·T10 은 back 레포에
있다)을 오류로 만들면 이 검사가 없던 규칙을 만들어 내는 셈이 된다.

재현 대상은 07-31 하루의 기록 그대로다.

    사고 1·4  T## 중복 (-219/-220 이 같은 번호, -223 이 세는 사이 -221 이 병합됨)
    사고 2    merge=union 이 갱신된 색인 행의 낡은 판을 되살려 같은 문서가 두 줄
    사고 3    번호 재조정 때 전수 표만 고쳐 파일 표가 낡음
"""
from pathlib import Path

import pytest
import yaml

from tools.check_docs_index import (
    INDEXES,
    DocsIndexError,
    check_repository,
)

ROOT = Path(__file__).parents[1]

TROUBLESHOOTING = """# 트러블슈팅

## 개별 문서

| 문서 | 내용 |
|---|---|
{file_rows}

## 문제 해결 — 전수 (AI 소유)

| T | 증상 | 해결 |
|---|---|---|
{ledger_rows}
"""

IMPLEMENTS = """# 구현 리포트

## 개별 리포트

| 문서 | 유형 | 내용 |
|---|---|---|
{file_rows}

## 구현·산출 — 전수 (AI 소유)

| I | 산출 | 반영처 |
|---|---|---|
{ledger_rows}
"""


def build_repo(tmp_path, *, files, indexed, numbers):
    """troubleshooting 만 인자대로 만들고 implements 는 항상 정합 상태로 둔다.

    `files` 는 실제로 만들 파일, `indexed` 는 파일 표에 넣을 행(중복을 만들려면 같은
    이름을 두 번 준다), `numbers` 는 전수 표의 첫 칸이다.
    """
    troubleshooting = tmp_path / "docs" / "troubleshooting"
    implements = tmp_path / "docs" / "implements"
    troubleshooting.mkdir(parents=True)
    implements.mkdir(parents=True)

    for name in files:
        (troubleshooting / name).write_text("# 본문\n", encoding="utf-8")

    (troubleshooting / "README.md").write_text(
        TROUBLESHOOTING.format(
            file_rows="\n".join(f"| [{name}]({name}) | 내용 |" for name in indexed),
            ledger_rows="\n".join(f"| T{number} | 증상 | 해결 |" for number in numbers),
        ),
        encoding="utf-8",
    )
    (implements / "README.md").write_text(
        IMPLEMENTS.format(
            file_rows="| [a.md](a.md) | 구현 | 내용 |",
            ledger_rows="| I1 | 산출 | 반영처 |",
        ),
        encoding="utf-8",
    )
    (implements / "a.md").write_text("# 본문\n", encoding="utf-8")
    return tmp_path


def findings_of(root, label="트러블슈팅"):
    return [
        finding
        for report in check_repository(root)
        for finding in report.findings
        if finding.label == label
    ]


def test_현재_레포의_색인이_정합이다():
    """CI 스텝과 같은 판정을 pytest 에서도 받는다. 실패하면 색인이 실물과 어긋난 것이다."""
    reports = check_repository(ROOT)
    findings = [finding for report in reports for finding in report.findings]
    assert findings == [], "\n".join(
        line for finding in findings for line in finding.render()
    )
    assert {report.spec.prefix for report in reports} == {"T", "I"}


def test_정상_상태와_결번을_통과시킨다(tmp_path):
    """T9·T10 은 back 레포 아티팩트라 결번이다. 연속성은 검사 대상이 아니다."""
    root = build_repo(
        tmp_path,
        files=["a.md", "b.md"],
        indexed=["a.md", "b.md"],
        numbers=[1, 2, 9, 12, 60],  # 3~8·10·11 결번
    )
    assert findings_of(root) == []


def test_사고_1_4_같은_번호를_두_번_쓰면_잡는다(tmp_path):
    """-219/-220 이 분기 시점 기준으로 같은 번호를 잡았고, -223 이 세는 사이 -221 이 병합됐다."""
    root = build_repo(
        tmp_path,
        files=["a.md"],
        indexed=["a.md"],
        numbers=[1, 53, 55, 53],  # 나란히 서지 않는다 — 전수 표는 정렬되지 않는다
    )
    findings = findings_of(root)
    assert [finding.kind for finding in findings] == ["번호 중복"]

    finding = findings[0]
    assert "T53" in finding.detail
    assert "2번" in finding.detail
    # 두 줄의 위치를 모두 지목해야 어느 행을 고칠지 사람이 고르지 않는다.
    assert finding.detail.count("README.md:") == 2
    # 「중복입니다」로 끝내지 않는다 — 다음 빈 번호와 병합 순서를 말한다.
    assert "T56" in finding.remedy
    assert "origin/dev" in finding.remedy


def test_사고_2_merge_union_이_되살린_중복_행을_잡는다(tmp_path):
    """색인 행을 **갱신**하면 union 드라이버가 낡은 판을 남겨 같은 문서가 두 줄이 된다(T56)."""
    root = build_repo(
        tmp_path,
        files=["a.md", "b.md"],
        indexed=["a.md", "b.md", "a.md"],
        numbers=[1],
    )
    findings = findings_of(root)
    assert [finding.kind for finding in findings] == ["색인 행 중복"]
    assert "a.md" in findings[0].detail
    assert "merge=union" in findings[0].remedy


def test_사고_3_파일_표가_낡으면_고아로_잡는다(tmp_path):
    """번호 재조정 때 전수 표만 고치면 파일 표가 뒤에 남는다 — 07-31 에 실제로 그랬다."""
    root = build_repo(
        tmp_path,
        files=["a.md", "b.md"],
        indexed=["a.md"],
        numbers=[1],
    )
    findings = findings_of(root)
    assert [finding.kind for finding in findings] == ["고아"]
    assert "b.md" in findings[0].detail
    # 붙여 넣을 수 있는 행을 준다.
    assert "| [b.md](b.md) |" in findings[0].remedy
    assert "개별 문서" in findings[0].remedy


def test_색인이_가리키는_파일이_없으면_잡는다(tmp_path):
    root = build_repo(
        tmp_path,
        files=["a.md"],
        indexed=["a.md", "사라진.md"],
        numbers=[1],
    )
    findings = findings_of(root)
    assert [finding.kind for finding in findings] == ["누락"]
    assert "사라진.md" in findings[0].detail


def test_한_번에_여러_위반을_전부_낸다(tmp_path):
    """첫 위반에서 멈추면 고치고 다시 돌리는 왕복이 위반 수만큼 늘어난다."""
    root = build_repo(
        tmp_path,
        files=["a.md", "b.md"],
        indexed=["a.md", "a.md", "없다.md"],
        numbers=[1, 1],
    )
    assert sorted(finding.kind for finding in findings_of(root)) == [
        "고아",
        "누락",
        "번호 중복",
        "색인 행 중복",
    ]


def test_전수_표의_번호_형식_위반을_조용히_넘기지_않는다(tmp_path):
    """`T53~T55` 처럼 묶으면 그 번호들이 세어지지 않는다 — 통과가 아니라 오류다."""
    root = build_repo(tmp_path, files=[], indexed=[], numbers=[1])
    readme = root / "docs" / "troubleshooting" / "README.md"
    readme.write_text(
        readme.read_text(encoding="utf-8").replace("| T1 |", "| T2~T4 |"),
        encoding="utf-8",
    )
    findings = findings_of(root)
    assert [finding.kind for finding in findings] == ["전수 표 형식"]


def test_섹션_제목이_바뀌면_통과가_아니라_실패다(tmp_path):
    """0건을 세고 통과하면 게이트는 이름만 남는다 — "확인하지 못했다"는 통과가 아니다."""
    root = build_repo(tmp_path, files=["a.md"], indexed=["a.md"], numbers=[1])
    readme = root / "docs" / "troubleshooting" / "README.md"
    readme.write_text(
        readme.read_text(encoding="utf-8").replace("## 개별 문서", "## 문서 목록"),
        encoding="utf-8",
    )
    with pytest.raises(DocsIndexError) as error:
        check_repository(root)
    assert "개별 문서" in str(error.value)
    assert "tools/check_docs_index.py" in str(error.value)


def test_색인이_없으면_실패다(tmp_path):
    root = build_repo(tmp_path, files=["a.md"], indexed=["a.md"], numbers=[1])
    (root / "docs" / "troubleshooting" / "README.md").unlink()
    with pytest.raises(DocsIndexError):
        check_repository(root)


def test_두_표의_목록_불일치는_검사하지_않는다(tmp_path):
    """표 이중화가 07-31 사고 3 의 **원인**이다. 일치 검사는 문제를 규칙으로 승격시킨다.

    각 표는 파일 시스템에만 대조한다. 전수 표에 링크가 없어도(있어도) 파일 표가
    정합이면 통과다 — 구조 판단은 중앙 몫이고 이 검사가 선점하지 않는다.
    """
    root = build_repo(tmp_path, files=["a.md"], indexed=["a.md"], numbers=[1, 2, 3])
    assert findings_of(root) == []


def test_다음_번호를_알려_준다(tmp_path):
    """번호를 세는 일 자체가 사고 1·4 의 원인이었다. 사람이 세지 않게 한다."""
    root = build_repo(tmp_path, files=[], indexed=[], numbers=[1, 60])
    report = next(r for r in check_repository(root) if r.spec.prefix == "T")
    assert report.highest == 60
    assert "다음 번호 T61" in report.summary()


def test_두_색인_모두_검사한다():
    assert {(spec.directory, spec.prefix) for spec in INDEXES} == {
        ("docs/troubleshooting", "T"),
        ("docs/implements", "I"),
    }


def test_ci_가_이_검사를_조건_없이_돌린다():
    """스텝이 빠지거나 조건이 붙으면 검사는 이름만 남는다(coverage 게이트와 같은 이유).

    필수 상태 검사는 `ai-ci / check` 와 `ai-ci / embedding profile parity` 둘뿐이므로
    (CONTRIBUTING 「PR과 리뷰」), 새 잡을 만들면 실패해도 병합을 막지 못한다. 그래서
    `check` 잡 **안에** 둔다.
    """
    workflow = yaml.load(
        (ROOT / ".github" / "workflows" / "ai-ci.yml").read_text(encoding="utf-8"),
        Loader=yaml.BaseLoader,
    )
    steps = workflow["jobs"]["check"]["steps"]
    step = next(
        step for step in steps if step.get("name") == "Docs index integrity"
    )
    assert step["run"].strip() == "python tools/check_docs_index.py"
    assert "if" not in step, "조건을 달면 특정 이벤트에서 조용히 건너뛴다"
