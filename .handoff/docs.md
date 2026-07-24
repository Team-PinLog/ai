# 문서 정합화 — 인계 문서 (handoff)

> **문서화 세션 → 다음 착수 세션** 인계물. 현재는 **대기 중**이며, 착수 조건이 충족되면
> 아래 「계획 전문」대로 실행한다. 이 문서 자체는 PR 없이 `chore/handoff-docs` 브랜치에만 둔다.

---

## 1. 현재 상태 — 대기 중

- 문서화 세션이 정합화 계획을 승인받고, **착수 조건 충족을 대기**하는 상태.
- 완료된 것: ① 절번호 드리프트 전수 점검 ② 계획 확정·승인 ③ 이 인계 문서 작성.
- 미착수: A~E 갭 편집 일체(아직 어떤 문서도 수정하지 않음).

## 2. 착수 조건

- **E3(`S15P11A705-33`)의 PR2(파이프라인 시나리오) 병합 = "E3 완료" 회신 수신**이 착수 트리거.
- E3는 **PR#17**로 자기 소관분(구현 리포트 I20·troubleshooting T19~T21·spec 상태 헤더·WORKLOG #14·#16 행·`search_path` 서술 3곳)을 **이미 병합**했다.
- 그러나 **PR2 미착수**라 `-33`은 여전히 **진행 중** → 착수 조건 미충족.

## 3. 착수 시 확인 항목 (확정값 — 계획 수정 없음)

E3 PR#17 병합으로 아래 3건은 이미 확정되었다. 착수 시 계획서의 해당 항목을 이 값으로 처리한다:

| 계획 항목 | 확정 |
|---|---|
| WORKLOG `tests/` 링크(C) | E3가 PR#17에서 `../tests/`로 **이미 정정 → 스킵** |
| `implements/README` I16 리포트 수(B) | I20 등재 완료 → **5로 확정** |
| WORKLOG #14·#16 행(C) | E3가 추가 완료 → 문서화는 **#12·#13·#15만** 추가 |

## 4. Jira 발급 정보 (착수 시)

- 제목: `[AI] 문서 정합화 — 전수 조사 갭 반영`
- parent: `S15P11A705-32`
- 라이프사이클: 착수 시 발급 → **진행 중** 전이, 편집·머지 후 **완료** 전이.

## 5. 절번호 드리프트 전수 결과 (별도 요청분 — 완결)

재구조화 부작용 드리프트는 **`ai/docs` 3건이 전부**다(모두 `architecture.md` "§2 시스템 맥락" 신설로 이후 절 +1 밀림):

| 출처 | 현재 표기 | 실제 |
|---|---|---|
| `spec/keyword-preset.md:28` | `architecture.md §4` | §5 Preset Cache 위치 |
| `spec/deletion-race-control.md:148` | `architecture.md §5` | §6 DB 세션 경계 |
| `implements/2026-07-23-fastapi-implementation.md:15` | `architecture.md §2 계층` | §3 모듈 구조 (신규 발견) |

- **back/docs/ai**(파일→파일 상호참조 8건, "N장"·"N.N" 표기) · **계약 `static/05_AI_설계.md`·`05-1` 참조**(ai 10 spec + back 5문서): **전부 정합, 드리프트 0**. spec 계약 참조는 정정 불필요.

---

## 6. 계획 전문 (승인본)

> 아래는 승인된 계획서 원문이다. §3의 확정값이 B/C 일부를 이미 해소한다(계획 자체는 수정하지 않음).

### Context

이 세션은 **문서화 세션**(인덱스·정합성·규약·소유 경계·미결 표 관리 + 주기적 누락·형식·스테일 점검)이다.
개별 구현 리포트는 작업 세션이 작성하고, 이 세션은 그 결과의 인덱스·정합만 맡는다.

E3(작업) 세션이 6개 문서 표면을 전수 감사해 **24건 갭**(high 4·med 9·low 11)을 찾았고,
그중 **E3 자기 소관분은 E3가 직접** 처리한다:
- 구현 리포트 I20, troubleshooting T19~T21, spec 상태 헤더, WORKLOG의 E3 행(#14·#16), `search_path` 서술 정정

**이 계획은 나머지 = 문서화 세션 몫**을 담는다. 추가로 이 세션이 별도 요청받은
**절번호 드리프트 전수 점검** 결과를 포함한다(위 §5).

#### 실행 시점 (확정)
E3(`S15P11A705-33`) **완료(PR2 병합) 회신 후 일괄** 처리한다. 지금 착수하면 E3의 PR2·문서 작업으로
대상이 또 바뀌어 재점검이 필요하다. Jira 티켓도 범위가 확정되는 착수 시점에 발급한다.

### 확정 갭 (문서화 세션 몫) — 모두 `ai` 레포 `origin/main` 기준 검증 완료

#### A. 절번호 드리프트 (파일 → 파일 상호참조)
근본 원인: `spec/architecture.md`에 "§2 시스템 맥락"이 신설되며 이후 절이 +1씩 밀렸는데,
이를 참조하던 문서 3곳이 옛 번호를 그대로 두었다(위 §5 표 3건). ai/docs 내 다른 파일→파일 참조 ~20건,
back/docs/ai 8건, 계약 참조 전부는 정합.

#### B. README·인덱스
- `docs/README.md:23` `## spec — 읽는 순서 (구현 예정 명세)` → **"구현 명세"** (E1·E2·E3 구현 완료, "예정"은 스테일)
- `reports/` 옛 폴더명이 **링크 표시 텍스트**에 잔존 4곳(href는 정상) → 표시 텍스트에서 `reports/` 제거:
  - `implements/2026-07-23-keyword-matching-eval.md:12`
  - `implements/2026-07-23-keyword-preset-seed.md:41`
  - `proposals/P26-keyword-preset-judgment.md:7`
  - `troubleshooting/mermaid-headless-validation.md:5`
- `implements/README.md` I16 `"…리포트 3"` → **5로 확정**(§3, I20 등재됨)
- README 미언급 산출 → `implements/` 전수표(I) 또는 레포 README에 반영: **schema/ 레이어, `tools/keyword_eval`, CI·Jira 키 검증 규약, `requirements-dev.txt`**

#### C. WORKLOG (`docs/WORKLOG.md`)
- **누락 행 추가**(실제 병합일 확인 완료): `#12` github conventions(07-24), `#13` M5 종결(07-24), `#15` 기록 보존 원칙(07-24). *(#14·#16 E3 행은 E3가 추가 완료 — §3)*
  - 사용자 언급 `#9 참조갱신`은 착수 시 실재/번호 확인 후 포함
- `tests/` 링크: E3가 PR#17에서 `../tests/`로 정정 완료 → **스킵**(§3)
- **날짜 오기**: 행18(M2)·19(contextId)·20(E3-PR1)이 `2026-07-23` 표기 → **실제 `2026-07-24`** (병합 로그로 확정)

#### D. proposals (`docs/proposals/README.md`)
전수표는 P14(`/api/core/v1`)·P20(`updated_at` 제거)처럼 계약성 결정을 P 번호로 등재하는 양식.
현재 ai 최대 P38, back에 P39 존재 → 신규는 **P40부터**.
- **P40 신규**: `/search` 응답 `contextId` 추가(DISTINCT ON, Spring matchedContext 조립용) — 반영처 `ai#11`·`docs#10`·`spec/personal-search.md`
- **P41 신규**: 툴체인 결정 — Python 3.12 통일·pgvector 0.8.1-pg16·requirements lock — 반영처 `ai#14`(E3-PR1)
- **P26 §영향 귀속 정정**: `P26:45` `"…투입한다(ai#6에서 반영)."` — 착수 시 git 이력(gemini 판정부 실제 투입 커밋)으로 올바른 PR 확정 후 정정

#### E. spec 모듈 트리 (E3 소관 외)
- `spec/architecture.md §3` 모듈 구조 코드트리에 실제 코드의 **`security.py`·`bootstrap/load_presets.py` 누락** → 실제 코드 트리 대조 후 추가

### 실행 순서 (E3 완료 회신 후)
1. Jira 발급 — `[AI] 문서 정합화 — 전수 조사 갭 반영`, `parent: S15P11A705-32`, 착수 시 진행 중 전이
2. E3 완료분(I20·T19~21·spec 헤더·search_path) 확인 후, 겹침 항목(§3의 3건) 확정값 적용
3. 컨벤션 브랜치(`docs/S15P11A705-XX-…`)에서 A~E 편집
4. `ai-ci`(ruff·compileall·pytest) 통과 + 내부 링크 무손상 확인
5. squash 병합, 머지 후 Jira 완료 전이, WORKLOG 자기 행 추가, 메모리 갱신

### 검증
- 링크 무결성: `docs/` 내 상대링크·`§N` 상호참조 재-grep 후 대상 헤딩과 재대조(드리프트 재발 0 확인)
- `git -C ai grep -nE "reports/" origin/main -- docs` → 표시 텍스트 잔존 0
- proposals 전수표 P 번호 연속성(P40·P41)과 `> back 위임` 주석 정합
- ai-ci green
