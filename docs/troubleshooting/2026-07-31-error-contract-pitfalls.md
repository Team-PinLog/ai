# 오류 응답 계약을 검증하며 만난 함정 (2026-07-31)

`S15P11A705-220` — 업스트림 실패를 HTTP 상태로 바꾸는 계약을 넣고, 그것을 **로컬에서 실제로
502 를 만들어** 확인하는 과정에서 걸린 셋이다.

셋 다 「검증 자체」의 함정이지 대상 코드의 결함이 아니다. **그래서 더 오래 걸린다** —
증상이 검증 대상의 문제처럼 보인다.

관련 구현 기록: [search-error-contract](../implements/2026-07-31-search-error-contract.md)

---

## T50. `httpx.ASGITransport` 는 기본값이 예외를 **응답으로 바꾸지 않는다**

"핸들러가 없으면 500 이 나간다"를 테스트로 단언하려 했는데 500 이 오지 않았다.
`pytest.raises` 도 아닌 자리에서 **예외가 테스트로 그대로 튀었다.**

```
app.core.errors.TransientError: embedding error: 502 provider says no
```

`raise_app_exceptions` 의 기본값이 `True` 라서다. ASGI 앱이 예외를 던지면 transport 가
그것을 다시 던진다. **uvicorn 은 그 자리에서 500 을 만든다** — 즉 기본값 그대로 쓰면
테스트가 운영과 다른 것을 본다.

```python
httpx.ASGITransport(app=app, raise_app_exceptions=False)   # 500 을 단언하려면 이것
```

기본값이 유용한 경우도 있다. RED 단계에서 **어느 예외가 어디까지 샜는지** 트레이스백으로
바로 보였고, 그것이 "분류는 맞고 변환만 없다"를 확인해 줬다. 두 값을 목적에 따라 쓴다.

> 「500 이 나가는가」를 보려면 `False`, 「무엇이 새는가」를 보려면 `True`.

## T51. 로컬 GMS 스텁도 URL 에 `/gmsapi/` 가 있어야 앱이 뜬다

게이트웨이를 대신할 스텁을 `http://127.0.0.1:8099/v1` 로 띄우고 `GMS_BASE_URL` 을 그리로
돌렸더니 **서버가 기동에서 죽었다.**

```
SettingsError: GMS_BASE_URL 형식 오류 — '/gmsapi/' 세그먼트가 없습니다.
```

`Settings._gms_base_url_shape` 가 기동 시 검사한다. 판정 클라이언트가 그 세그먼트 앞을 잘라
Gemini 네이티브 root 를 파생하기 때문이고, 없으면 **임베딩만 동작하고 judge 가 조용히
실패**하는 비대칭 장애가 된다 — 검사가 옳다.

스텁이 그 경로 모양을 그대로 흉내내면 된다.

```python
Route("/gmsapi/api.openai.com/v1/embeddings", handler, methods=["POST"])
```

```bash
GMS_BASE_URL="http://127.0.0.1:8099/gmsapi/api.openai.com/v1"
```

**게이트웨이를 로컬로 대체하는 모든 재현이 이 제약을 받는다.** 스텁 URL 을 임의로 정하면
증상이 「스텁이 안 불린다」가 아니라 「앱이 안 뜬다」로 나와 한 단계 멀어진다.

## T52. 시연 DB 비밀번호는 `pinlog-local` 이다 — `.env` 값과 다르다

T33 이 `ai/.env` 의 `DATABASE_URL` 이 07-27 잔재 `:5433` 을 가리킨다는 것을 남겼다.
포트를 `:15432` 로 고쳐 붙었는데도 이번엔 이렇게 죽었다.

```
asyncpg.exceptions.InvalidPasswordError: password authentication failed for user "pinlog"
```

**포트만 바꾸면 안 된다.** `.env` 의 DSN 은 사용자·비밀번호도 죽은 하네스 것이다.

```
.env          postgresql://pinlog:pinlog@localhost:5433/pinlog
시연 정본      postgresql://pinlog:pinlog-local@localhost:15432/pinlog
```

값은 컨테이너에서 직접 읽는 것이 확실하다.

```bash
docker inspect pinlog-demo-postgres-1 --format '{{range .Config.Env}}{{println .}}{{end}}'
```

`-174` §7 절차가 매 명령에 DSN 전체를 덮어써서 지금까지 드러나지 않았다. **DSN 의 일부만
고치는 방식이 이 함정을 만든다** — 전체를 덮어쓰거나 전부 확인한다.
