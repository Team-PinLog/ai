# FastAPI 로컬 검증에서 겪은 인코딩·드라이버 경계 문제 (T16~T18)

- **상태**: 해결됨
- **날짜**: 2026-07-23
- **맥락**: FastAPI 구현(ai#5·#6)을 로컬 pgvector + 실제 GMS로 end-to-end 검증하는 과정
- **관련**: [implements/2026-07-23-fastapi-implementation.md](../implements/2026-07-23-fastapi-implementation.md)

## T16 — `.env` UTF-8 BOM으로 첫 키 파싱 실패

**증상**: pydantic-settings가 `DATABASE_URL` 필드를 "missing"으로 판정해 기동에 실패했다. 나머지 키(`GMS_API_KEY` 등)는 정상 인식됐다.

**원인**: PowerShell 5.1의 `Set-Content -Encoding UTF8`은 **BOM(EF BB BF)을 파일 앞에 붙인다**. 그 결과 첫 줄 키 이름이 `﻿DATABASE_URL`이 되어 키 매칭에 실패한다. 둘째 줄부터는 정상이므로, 첫 키만 누락되는 형태로 드러난다.

**해결**: BOM 없이 기록한다.
```powershell
$enc = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllLines("$PWD\.env", $lines, $enc)
```
검증은 파일의 첫 3바이트가 `68,65,84`(“DAT”)인지 확인하는 방식으로 한다. BOM이 붙어 있으면 `239,187,191`이 나온다.

## T17 — pgvector가 VECTOR 컬럼을 `Vector` 객체로 반환

**증상**: Preset 캐시 적재 시 `np.asarray(row["embedding"], dtype=np.float32)`에서
`TypeError: float() argument must be a string or a real number, not 'Vector'`가 발생했다.

**원인**: `pgvector.asyncpg.register_vector`가 VECTOR 컬럼을 numpy 배열이 아니라 `pgvector.Vector` 객체로 디코드한다. `np.asarray`가 이 객체를 처리하지 못한다.

**해결**: `to_numpy()`(또는 `to_list()`)로 변환한다. 반환형이 달라질 수 있으므로 방어적으로 감싼다.
```python
def _to_array(value) -> np.ndarray:
    if hasattr(value, "to_numpy"):
        return value.to_numpy().astype(np.float32)
    return np.asarray(value, dtype=np.float32)
```
이 문제는 디코드(읽기) 방향에서만 발생한다. 바인딩(쓰기) 방향은 `list[float]`를 그대로 넘겨도 된다.

## T18 — asyncpg `now() - $2` interval 타입 추론 실패

**증상**: 상태 전이 UPDATE의 `updated_at < now() - $2`에서
`UndefinedFunctionError: operator does not exist: timestamp with time zone < interval`이 발생했다.

**원인**: asyncpg는 prepared statement를 준비할 때 파라미터 타입을 전달된 값이 아니라 **SQL 문맥**으로 추론한다. `now() - $2`에서 `$2`의 타입이 미정이면 PostgreSQL이 `timestamptz - timestamptz = interval` 해석을 골라 `now() - $2` 전체가 interval 타입이 된다. 그러면 좌변 `timestamptz`와의 비교가 성립하지 않는다. 파이썬에서 `timedelta`를 넘겨도 소용이 없다. 타입 결정은 값이 전달되기 전인 준비 단계에서 끝나기 때문이다.

**해결**: 파라미터에 명시적 캐스트를 준다.
```sql
AND updated_at < now() - $2::interval
```
`timedelta`는 interval 로 인코딩되므로 `$2::interval`과 호환된다.

## 공통 교훈

세 건 모두 **로컬 실행 없이 코드 리뷰만으로는 드러나지 않는** 런타임과 드라이버 경계의 문제였다. pgvector·asyncpg·PowerShell 인코딩은 재발 가능성이 높다. 신규 구현 시에는 실제 컨테이너와 실제 드라이버로 최소 1회 end-to-end 를 돌려 확인한다.
