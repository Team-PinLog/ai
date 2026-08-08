# 오류 응답 계약을 검증하며 만난 함정 (2026-07-31)

`S15P11A705-220` — 업스트림 실패를 HTTP 상태로 바꾸는 계약을 넣고, 그것을 **로컬에서 실제로
502 를 만들어** 확인하는 과정에서 만난 문제들이다.

앞의 세 건은 「검증 자체」의 함정이지 대상 코드의 결함이 아니다. **그래서 더 오래 걸린다.**
증상이 검증 대상의 문제처럼 보이기 때문이다.

관련 구현 기록: [search-error-contract](../implements/2026-07-31-search-error-contract.md)

---

## T50. `httpx.ASGITransport` 는 기본값이 예외를 **응답으로 바꾸지 않는다**

"핸들러가 없으면 500 이 나간다"를 테스트로 단언하려 했는데 500 이 오지 않았다.
`pytest.raises` 도 아닌 자리에서 **예외가 테스트 코드까지 그대로 전파됐다.**

```
app.core.errors.TransientError: embedding error: 502 provider says no
```

`raise_app_exceptions` 의 기본값이 `True` 라서다. ASGI 앱이 예외를 던지면 transport 가
그것을 다시 던진다. 반면 **uvicorn 은 그 자리에서 500 응답을 만든다.** 즉 기본값 그대로 쓰면
테스트가 운영과 다른 동작을 보게 된다.

```python
httpx.ASGITransport(app=app, raise_app_exceptions=False)   # 500 을 단언하려면 이것
```

기본값이 유용한 경우도 있다. RED 단계에서 **어느 예외가 어디까지 전파됐는지** 트레이스백으로
바로 보였고, 그것이 "분류는 맞고 변환만 없다"를 확인해 줬다. 두 값을 목적에 따라 쓴다.

> 「500 이 나가는가」를 보려면 `False`, 「무엇이 새는가」를 보려면 `True`.

## T51. 로컬 GMS 스텁도 URL 에 `/gmsapi/` 가 있어야 앱이 뜬다

게이트웨이를 대신할 스텁을 `http://127.0.0.1:8099/v1` 로 띄우고 `GMS_BASE_URL` 을 그리로
돌렸더니 **서버가 기동에 실패했다.**

```
SettingsError: GMS_BASE_URL 형식 오류 — '/gmsapi/' 세그먼트가 없습니다.
```

`Settings._gms_base_url_shape` 가 기동 시 검사한다. 판정 클라이언트가 그 세그먼트 앞을 잘라
Gemini 네이티브 root 를 파생하기 때문이다. 세그먼트가 없으면 **임베딩만 동작하고 judge 는
오류 신호 없이 실패**하는 비대칭 장애가 되므로, 이 기동 검사는 옳다.

스텁이 그 경로 모양을 그대로 흉내내면 된다.

```python
Route("/gmsapi/api.openai.com/v1/embeddings", handler, methods=["POST"])
```

```bash
GMS_BASE_URL="http://127.0.0.1:8099/gmsapi/api.openai.com/v1"
```

**게이트웨이를 로컬로 대체하는 모든 재현이 이 제약을 받는다.** 스텁 URL 을 임의로 정하면
증상이 「스텁이 안 불린다」가 아니라 「앱이 안 뜬다」로 나와, 원인에서 한 단계 멀어진다.

## T52. 시연 DB 비밀번호는 `pinlog-local` 이다 — `.env` 값과 다르다

T33 이 `ai/.env` 의 `DATABASE_URL` 이 07-27 잔재 `:5433` 을 가리킨다는 것을 남겼다.
포트를 `:15432` 로 고쳐 접속이 됐는데도 이번에는 이렇게 실패했다.

```
asyncpg.exceptions.InvalidPasswordError: password authentication failed for user "pinlog"
```

**포트만 바꾸면 안 된다.** `.env` 의 DSN 은 사용자·비밀번호도 이미 폐기된 하네스의 것이다.

```
.env          postgresql://pinlog:pinlog@localhost:5433/pinlog
시연 정본      postgresql://pinlog:pinlog-local@localhost:15432/pinlog
```

값은 컨테이너에서 직접 읽는 것이 확실하다.

```bash
docker inspect pinlog-demo-postgres-1 --format '{{range .Config.Env}}{{println .}}{{end}}'
```

`-174` §7 절차가 매 명령에 DSN 전체를 덮어써서 지금까지 드러나지 않았다. **DSN 의 일부만
고치는 방식이 이 함정을 만든다.** 전체를 덮어쓰거나 전부 확인한다.

## T56 — `merge=union` 은 같은 행을 **갱신**해도 두 판을 남긴다

**증상** 색인 표에 같은 문서가 두 번 나온다. 번호도 링크도 정상인데 행만 둘이다.

**원인** `merge=union` 은 충돌을 「양쪽 다 채택」으로 푼다. 새 행을 각자 추가한 경우에는 그것이 정확히 원하는 동작이다. 그런데 **한쪽이 기존 행을 고치면** git 은 그것을 「지운 것 + 새로 넣은 것」으로 보고, 지운 쪽 행을 되살린다.

```
-219 작업 중        … 사전 기준 (T43~T46)
-219 마무리에 갱신   … 사전 기준·1회 분포·번호 충돌 (T43~T49)
merge=union 결과    두 줄 다
```

**대응** 색인 행을 **고칠 때**는 병합 뒤에 중복을 확인한다. 추가만 할 때는 안심해도 된다.

```bash
awk -F'|' '/^\| \[/ {print $2}' docs/troubleshooting/README.md | sort | uniq -d
```

같은 날 발생한 `T43` 번호 충돌(T48)과는 원인이 다르다. 그쪽은 두 브랜치가 **같은 번호를 각자 잡은 것**이고, 이쪽은 **한 브랜치가 자기 행을 갱신한 것**이다. 전자는 union 이 못 막고 후자는 union 이 만든다.
