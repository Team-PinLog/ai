# 멀티세션 워킹트리 오염 · import 시점 `.env` 캐시

- **상태**: 해결됨 (워크플로·부팅 교정)
- **날짜**: 2026-07-28 (복원 등재 — 실제 발생 2026-07-24~27)
- **관련**: 메모리 `pinlog-multisession-worktree`, [S1 복원 리포트](../implements/2026-07-28-s1-implementation-recovery.md)
- **레이어**: git 워크플로 · 앱 부팅

> 종료된 S1 세션이 겪었으나 레포 troubleshooting에는 없던 2건을 복원한다. 전부 `기록복원`.

## T25 — 멀티세션이 단일 워킹트리를 공유해 커밋 오염

**증상**: 내 3파일 커밋에 **타 세션 파일 15개가 섞여** push됨. force-push 직전 다른 세션이 브랜치를 전환해 커밋 무산.

**근본 원인**: 여러 세션이 **하나의 git 워킹트리·인덱스·HEAD를 공유**한다. `git add`·`checkout`·커밋이 세션 간에 경쟁한다.

**사전 미발견 이유**: 단일 세션 전제의 git 워크플로. 다른 세션은 이미 worktree 격리를 쓰고 있었으나 그 사실이 공유되지 않았다.

**해결**: 멀티 세션에서 **격리 `git worktree`를 기본**으로. 커밋 전 `git branch --show-current` 확인, `git add`는 개별 파일(`-A` 금지), 원격 병합은 로컬 전환 없이 `gh pr merge`로.

## T26 — 모듈 레벨 `create_app()` import 시점 `.env` 캐시 → API 3건만 401

**증상**: API 테스트 **3건만 401**(나머지는 통과라 원인이 혼동됨).

**근본 원인**: `main.py`의 모듈 레벨 `app = create_app()`가 **import 시점**에 `get_settings()`로 `.env`를 읽어 캐시한다. 테스트가 secret을 바꿔도 이미 캐시된 값이 쓰인다.

**사전 미발견 이유**: 로컬에 `.env`가 있어 값이 **우연히 맞았다**. CI엔 `.env`가 없어 conftest가 placeholder env를 선주입하는데, 그 값과 테스트 기대가 어긋난 지점만 401로 드러났다.

**해결**: `settings` fixture에서 `get_settings()` 캐시를 재설정(재사용 방지), conftest에서 placeholder env를 import 전에 선주입. (관련: T16·T22 계열 — 환경/인코딩이 일부 요청만 깨뜨려 혼동시키는 패턴)
