# 죽은 설정 키 전수조사 — 함정 (T66·T67)

`S15P11A705-224` 작업 중 겪은 두 함정. 구현 리포트는
[dead-config-keys.md](../implements/2026-08-03-dead-config-keys.md).

## T66 — alias 매칭은 grep으로 못 잡는다

pydantic Settings는 필드를 소문자 속성명으로 선언해도 `alias=`로 지정한 대문자 env
이름을 읽는다(`populate_by_name=True`와 무관하게 alias가 우선). 문제는 반대 방향이다
— **`.env`나 문서에 대문자 키 이름이 리터럴로 남아 있어도, `config.py`에 그 alias를
가진 필드가 실제로 있는지는 grep으로는 확인되지 않는다.** `PINLOG_JUDGE_MODEL`이
`.env.example`·여러 문서·`config.py` 주석에 문자열로 등장하지만(전부 "이제 안 읽는다"는
설명 맥락), 그 문자열 존재 자체는 "읽힌다"의 증거가 아니다.

`-210`이 "임계값이 없다"고 적었다가 실제로는 `config.py:114`에 있었던 사고와 정반대
모양이다 — 그쪽은 없다고 했는데 있었고, 이쪽은 있어 보이는데(그럴듯한 값) 없다.
둘 다 문자열 서치만으로 판단해 생긴 오판이다.

**대응**: 각 후보 키에 sentinel 값을 주입해 실제로 `Settings()`를 생성하고
`Settings.model_fields`와 필드 값을 확인했다. `os.environ`을 전부 비우면 Windows의
`asyncio.windows_events`가 `SYSTEMROOT` 등을 못 찾아 `OSError`가 나므로, 필요한 키만
얹었다 떼는 방식으로 검증 환경을 구성했다.

## T67 — "N개 대 M개"의 차집합은 한 방향이 아닐 수 있다

계약은 "`.env` 13키, `.env.example` 12키 — 어느 키가 example에 없는지 찾아 필요하면
추가한다"고 단일 방향 차집합(13 - 12 = 1)을 가정했다. 실제로는 `.env`에만 있는 키가
2개(`PINLOG_JUDGE_MODEL`·`PRESET_CACHE_TTL_SEC`, 둘 다 죽은 키)였고 `.env.example`에만
있는 키가 1개(`PINLOG_JUDGE_CHAIN`, 살아 있는 키)였다 — 공통 11개 + 2 = 13,
공통 11개 + 1 = 12로 총계는 맞지만 "하나만 다르다"는 전제와 실제 구성이 다르다.

숫자 차이(13 vs 12 = 1)만 보고 "example에 하나를 더 채워 넣으면 끝"이라고 예단하면
틀린다 — 둘 다 셋으로 나눠(공통·`.env`만·`.env.example`만) 각각 확인해야 어느 쪽이
죽었고 어느 쪽이 "추가할 필요 없는 정상 생략"인지 갈린다.

**대응**: `.env`와 `.env.example`의 키 이름 집합을 각각 뽑아 대칭차(symmetric
difference)로 비교했다. 결론은 두 방향 다 "정상"이었다 — `.env`의 2개는 죽은 키라
`.env.example`에 추가할 필요가 없고, `.env.example`의 1개(`PINLOG_JUDGE_CHAIN`)는
`.env`가 코드 기본값에 의존하기로 한 정상 생략이다.

> T9(H2·pgvector)·T10(flyway.schemas)와 같은 표기 규칙을 따른다 — 상세는 전수 표
> `docs/troubleshooting/README.md`.
