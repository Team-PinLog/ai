# 로컬 전 스택 E2E 와 CI 에서 만난 함정 (2026-07-31)

`front` → `back` → FastAPI → GMS → pgvector 전 경로를 브라우저로 처음 돌리면서, 그리고
`dev` → `main` 릴리스와 CI 검사를 넣으면서 만난 것들이다.

**여덟 개 중 넷은 「기대한 출력이 없다」가 증상이었고 원인은 제각각이었다.** 그것이
이 문서를 한 편으로 묶은 이유다 — 하나를 겪으면 다음 것을 오진하게 된다.

관련 구현 기록: [seed-guard](../implements/2026-07-31-seed-guard.md) ·
[gms-call-observability](../implements/2026-07-31-gms-call-observability.md) ·
[embedding-grid](../implements/2026-07-31-embedding-grid.md)

---

## T29. `python -m uvicorn` 이 시스템 Python 을 타서 `No module named uvicorn`

재현 절차([real-data-e2e §7](../implements/2026-07-30-real-data-e2e.md))에 `python` 으로
적혀 있었다. Windows 에서 그것은 `C:\Python314\python.exe` 이고 **가상환경이 아니다.**

```
C:\Python314\python.exe: No module named uvicorn
```

**exit code 가 0 이다.** 백그라운드로 띄우면 「완료」로 보이고 포트만 안 열린다.

```bash
ai/.venv/Scripts/python.exe -m uvicorn app.main:app --port 8000
```

절차 문서의 `python` 을 전부 이 경로로 읽어야 한다.

## T30. 백그라운드 파이프가 서버 로그를 버퍼에 가둔다

`uvicorn ... 2>&1 | tail -40` 으로 띄우면 **프로세스가 살아 있는 동안 출력 파일이 0바이트**다.
`tail` 이 EOF 를 기다리기 때문이다. `back` 도 같다.

로그를 보려면 파이프가 아니라 리디렉션으로 띄운다.

```bash
uvicorn app.main:app --port 8000 > .demo/uvicorn.log 2>&1
```

**T31 을 오진하게 만든 원인이 이것이다.**

## T31. `gms window` 가 안 나오는 것을 계측 실패로 오판했다

기록을 3건 만들고 로그를 봤더니 `gms call` 도 `gms window` 도 없었다. 계측이 실동작하지
않는다고 판정했는데 **틀렸다.**

설계가 그렇다 — 성공 호출은 DEBUG 고, 분모는 60초 창 집계 한 줄만 INFO 이며,
**창을 닫는 것은 타이머가 아니라 다음 호출**이다. 3건이 3초 안에 들어가면 같은 창에
담기고 그 창은 아직 열려 있다.

60초를 기다린 뒤 1건을 더 넣자 그 자리에서 나왔다.

```
INFO app.client.gms gms window window=324s calls=7 fail=0 fail_pct=0
     avg_ms=1940 max_ms=2219 ok=7 [embedding ok=4] [judge:openai ok=3]
```

**`[judge:openai]` 가 이 한 줄의 값이다** — 폴백 체인 1순위가 실제로 선택된 것을 보여준다.

> 검증이 실패했는지 검증 방법이 틀렸는지를 먼저 갈라야 한다. 이 날 훅·CI 셋이
> **자기 테스트를 통과하고 실전에서 무력했기 때문에** 같은 증상을 결함으로 읽는
> 편향이 있었다.

## T32. `back` jar 이 낡으면 Flyway 가 기동을 거부한다

`back` 을 당긴 뒤 옛 jar 로 띄우면 이렇게 죽는다.

```
Detected applied migration not resolved locally: 6
```

DB 에는 `V6` 가 적용돼 있는데 jar 안에 그 파일이 없어서다. **DB 문제로 보이지만
빌드 산출물 문제다.**

```bash
cd back && ./gradlew bootJar
```

`git pull` 뒤에는 항상 재빌드한다. 이 날 `S15P11A705-198` 작업 중 실제로 겪었고,
진단은 우리 도구가 아니라 back 스택트레이스가 했다.

## T33. `ai/.env` 의 `DATABASE_URL` 이 시연 DB 를 가리키지 않는다

로컬 pgvector 가 둘이고 `.env` 는 **07-27 잔재인 `:5433`** 을 가리킨다. 시연 정본은
`:15432`(`pinlog-demo-postgres-1`)다.

`real-data-e2e §7` 절차가 매 명령에 `DATABASE_URL` 을 덮어써서 지금까지 안 터졌다.
**한 번 빠뜨리면 조용히 다른 DB 에 붙는다.**

`seed.py` 의 preflight 가 접속 대상을 찍고 `:5433` 이면 BLOCK 하므로 이제 조용히
지나가지는 않는다([seed-guard](../implements/2026-07-31-seed-guard.md)). 기본값 자체는
그대로다.

## T34. 브라우저 로컬 테스트에서 로그인 판정은 `logged_in=1` 이다

소셜 OAuth 는 콜백 URL 이 운영 기준이라 로컬에서 돌지 않는다. 데모 JWT 키로 토큰을
만들어 쿠키에 심으면 우회된다.

**`access_token` 만 심으면 안 된다.** 프론트는 그것을 읽지 못하고(HttpOnly 전제)
별도 표시 쿠키로 판정한다.

```
access_token=<mint_access_token(member_id, pem)>   back 인증용
logged_in=1                                        프론트 라우트 가드용
XSRF-TOKEN=<임의>                                   CSRF 인터셉터용
```

**값이 정확히 `1` 이어야 한다.** `front` 의 `getIsLoggedIn.ts` 가
`document.cookie.split('; ').includes('logged_in=1')` 로 문자열 일치를 본다 —
`logged_in=true` 는 안 걸린다.

`vite.config.ts` 의 proxy target 도 기본이 `https://pin-log.com` 이라 로컬 back 을
쓰려면 `http://localhost:8080` 으로 바꿔야 한다(프론트 파트 소유 파일이므로 커밋하지 않는다).

## T35. 검색은 소유자별로 갈린다 — 계정을 잘못 잡으면 데이터가 없는 것처럼 보인다

실사용자 기록으로 검색했는데 가공 데모 6건만 나왔다. **`host` 계정으로 로그인했기
때문**이고, 실데이터는 다른 member 소유였다.

```sql
select sa.provider_user_id, sa.member_id,
       (select count(*) from core.context c where c.member_id = sa.member_id)
from core.social_account sa order by 3 desc;
```

`verify.py` 가 질의에 `as` 필드를 받는 이유가 이것이다
([real-data-e2e](../implements/2026-07-30-real-data-e2e.md)).

## T36. CI 검사를 `dev` 에만 넣으면 `main` 기반 PR 에 적용되지 않는다

`main` 을 base 로 하는 일반 PR 을 막는 검사를 `ai-ci.yml` 에 넣고 `dev` 로 병합했는데,
직후 열린 `base=main` PR 이 **그대로 통과했다.**

**PR 의 CI 는 head 브랜치의 워크플로로 돈다.** `main` 에서 분기한 head 에는 그 검사가
파일에 없다. 즉 **정확히 막으려던 대상에게만 적용되지 않는다.**

`hotfix/*` 로 `main` 에 직접 올려 해소했다(그 브랜치명이 검사가 허용하는 첫 사례이기도 하다).

같은 파일에서 하나 더 — `ai-ci.yml` 전체 텍스트에 `:main` 이 있으면
`test_ci_image_publish_contract` 가 실패한다(이미지 태그 오염 방지). 오류 메시지에 쓴
`::error::main ...` 이 그 패턴에 걸렸다. **검사가 옳고 문구가 틀렸다.**
