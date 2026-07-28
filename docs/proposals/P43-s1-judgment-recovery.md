# P43: S1 구현 판단 변경·기각 대안 복원

- **상태**: Accepted (복원 기록)
- **날짜**: 2026-07-28 (복원 — 실제 2026-07-23~27)
- **주도(Driver)**: AI
- **관련 PR/커밋**: S1 세션 전반(ai#5~#24)
- **근거**: `pinlog/.claude/state/S1-RECOVERY-PACKET.md`

> 종료된 S1 세션의 **판단 변경 10건·기각 대안 9건**을 복원한다. 개별 계약 결정은 이미 P1~P41에 있고, 여기는 *"무엇에서 무엇으로, 왜 바뀌었고, 무엇을 왜 버렸는가"*를 남긴다.
> 출처: `기록복원`(transcript) · `추정`. 근거 없는 판단 이유는 창작하지 않는다.

## 판단 변경 10건 (전부 `기록복원`)

| # | 변경 | 계기·근거 |
|---|---|---|
| 1 | `search_path = ai` → `ai, public` | API 3건 401 → `type "vector" does not exist`로 단계적 좁혀짐. 처음엔 register_vector 타이밍으로 보고 **명시 캐스트로 방어**하려다, 캐스트를 넣자 진짜 원인(search_path)이 노출 → **캐스트 원복**하고 search_path만 고침(app 유일 변경). "이전 로컬 검증이 통과한 건 우연히 첫 커넥션만 register됐기 때문" (T21) |
| 2 | lock `uv pip compile` → `--universal` | 병합 후 main CI 19초 실패(`No matching distribution for pywin32`). Windows lock이 플랫폼 마커 없이 Windows 전용 고정 (T19) |
| 3 | PYTHONPATH 수동 → `pyproject pythonpath=["."]` | `ModuleNotFoundError: app`. **"로컬 PYTHONPATH 수동 설정이 CI 조건 차이를 가렸다"**고 자인, PYTHONPATH 없이 재현해 27 통과 확인 후 올림 (T20) |
| 4 | 로컬 `.venv` 3.14 → 3.12 재생성 | 환경 3분할(로컬/CI/미래) 인식 |
| 5 | 인계 문서 작성 → 인계 취소 | 사용자 지시 변경. 인계 브랜치 2개 삭제 |
| 6 | 문서 전수 감사 자가 수행 → 문서화 세션 위임 | 역할 분리 지시. 자기 구현 지식이 필요해 위임 불가한 것만 처리 |
| 7 | `/search` contextId "개인정보 경계" 우려 → 문제 없음 | contextId는 본인 데이터 식별자·수신자는 내부 Spring·본문은 여전히 Spring이 core에서 조회. **id 없이는 Spring이 조립을 못 해 원칙이 무력화** |
| 8 | 시나리오 5 "text 대조 WARN" 전제 → call 0·state 불변 | 커밋 전 코드 확인 시 **text 대조·WARN 경로가 실제로 없었음**. app 무변경 원칙을 지켜 단언을 코드 실상에 맞춤 |
| 9 | "pgvector 0.8.1 = back과 일치" → 무효화, 0.8.5+digest 권고 | back#31이 compose를 올림. 조사 끝에 digest까지 동일 고정을 권고했으나 **값 변경은 하지 않고 종료** |
| 10 | 공유 워킹트리 커밋 → 격리 worktree | `git add`한 3파일 커밋에 타 세션 파일 15개가 섞여 push, force-push 직전 브랜치 전환으로 무산 (T25) |

## 기각한 대안 9건

| 대안 | 기각 이유 | 출처 |
|---|---|---|
| 롤링 `pg16` 태그 | 재현성 파괴, 통일 비용 0 | `기록복원` |
| Preset Cache TTL 재적재 구현 | `architecture.md §5`가 "재시작으로만"으로 확정 | `기록복원` |
| 크로스레포 스키마 자동 diff | cross-repo 체크아웃 비용, MVP 과함 → back PR 템플릿 체크 + 스냅샷 헤더로 대체(back 템플릿 수정은 back 소관이라 **제안만**) | `기록복원` |
| 스냅샷 헤더 주석만 두는 안 | "코드가 스냅샷을 추월할 때만" 잡히고 **back이 앞선 구간은 침묵** | `기록복원` |
| CONTRIBUTING 지금 작성 | 규약 실체가 이미 docs/에 있고 1인 시점엔 과함 → README로 갈음 | `기록복원` |
| 기능별 폴더 분리 | 기능이 하나라 과설계(YAGNI). 계층이 이미 확장 축 | `기록복원` |
| 무거운 ML 의존성 처리 지금 결정 | 스택 확정 시점(합류)이 맞음. 원칙만 메모 | `기록복원` |
| `$3::vector` 명시 캐스트 방어 | **원복함** — 근본 원인이 search_path였음이 드러나 불필요해짐 | `기록복원` |
| SQLAlchemy 계열 | asyncpg가 채택됐으나 **대비 대안이 정확히 무엇이었는지, 기각 사유 발화는 없음** | **`추정`** |
