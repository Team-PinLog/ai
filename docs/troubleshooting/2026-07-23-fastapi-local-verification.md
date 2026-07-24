# FastAPI 로컬 검증 중 겪은 문제 (T16~T18)

- **상태**: 해결됨
- **날짜**: 2026-07-23
- **맥락**: FastAPI 구현(ai#5·#6)을 로컬 pgvector + 실제 GMS로 end-to-end 검증하는 과정
- **관련**: [implements/2026-07-23-fastapi-implementation.md](../implements/2026-07-23-fastapi-implementation.md)

## T16 — `.env` UTF-8 BOM으로 첫 키 파싱 실패

**증상**: pydantic-settings가 `DATABASE_URL` 필드를 "missing"으로 판정해 기동 실패. 나머지 키(`GMS_API_KEY` 등)는 정상 인식.

**원인**: PowerShell 5.1의 `Set-Content -Encoding UTF8`은 **BOM(EF BB BF)을 파일 앞에 붙인다**. 첫 줄 키 이름이 `﻿DATABASE_URL`이 되어 매칭에 실패한다. 둘째 줄부터는 정상이라 첫 키만 누락되는 형태로 드러난다.

**해결**: BOM 없이 기록한다.
```powershell
$enc = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllLines("$PWD\.env", $lines, $enc)
```
검증: 첫 3바이트가 `68,65,84`(“DAT”)인지 확인(BOM이면 `239,187,191`).

## T17 — pgvector가 VECTOR 컬럼을 `Vector` 객체로 반환

**증상**: Preset 캐시 적재 시 `np.asarray(row["embedding"], dtype=np.float32)`에서
`TypeError: float() argument must be a string or a real number, not 'Vector'`.

**원인**: `pgvector.asyncpg.register_vector`가 VECTOR 컬럼을 numpy가 아니라 `pgvector.Vector` 객체로 디코드한다. `np.asarray`가 이를 처리하지 못한다.

**해결**: `to_numpy()`(또는 `to_list()`)로 변환. 방어적으로 감싼다.
```python
def _to_array(value) -> np.ndarray:
    if hasattr(value, "to_numpy"):
        return value.to_numpy().astype(np.float32)
    return np.asarray(value, dtype=np.float32)
```
바인딩(쓰기) 방향은 `list[float]`를 그대로 넘겨도 되며, 디코드(읽기) 방향에서만 발생한다.

## T18 — asyncpg `now() - $2` interval 타입 추론 실패

**증상**: 상태 전이 UPDATE의 `updated_at < now() - $2`에서
`UndefinedFunctionError: operator does not exist: timestamp with time zone < interval`.

**원인**: asyncpg는 prepared statement 준비 시 파라미터 타입을 값이 아니라 **SQL 문맥**으로 추론한다. `now() - $2`에서 `$2`가 미정이면 PostgreSQL이 `timestamptz - timestamptz = interval`로 골라 `now() - $2`가 interval이 되고, 좌변 `timestamptz`와 비교가 성립하지 않는다(파이썬에서 `timedelta`를 넘겨도 준비 단계에선 무관).

**해결**: 파라미터에 명시적 캐스트를 준다.
```sql
AND updated_at < now() - $2::interval
```
`timedelta`는 interval로 인코딩되므로 `$2::interval`과 호환된다.

## 공통 교훈

세 건 모두 **로컬 실행 없이 코드 리뷰만으로는 드러나지 않는** 런타임/드라이버 경계 문제였다. pgvector·asyncpg·PowerShell 인코딩은 재발 가능성이 높아, 신규 구현 시 실제 컨테이너 + 실제 드라이버로 최소 1회 end-to-end를 돌려 확인한다.
