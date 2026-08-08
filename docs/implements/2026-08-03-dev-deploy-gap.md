# dev 병합이 배포에 반영되지 않는 구간 — `ai-image / publish` SKIPPED 는 설계이고, 프리셋 봉인 값은 수동 갱신 대상이다

- **상태**: 완료
- **날짜**: 2026-08-03
- **유형**: 감사 — 만든 것이 아니라 어디서 끊겼는가를 판정한 결과다. 산출물은 새 봉인 값 하나다.
- **기준 리비전**: `ai` `origin/dev` **d87c5f5** · `ai` `origin/main` **4d667c3** · `infra` `origin/main` **bd75090**
- **읽는 순서**: §1 이 "고장인가 설계인가"의 답이고, §4 가 산출물이다. §2 는 이 건의 성격(성공 신호 ≠ 동작)을 다룬다.

> `infra` 는 읽기만 했다. 이 문서의 `infra` 인용은 전부 `origin/main` `bd75090` 기준이며,
> 로컬 워킹트리(`d5ebef3`)는 뒤처져 있어 쓰지 않았다.

---

## 0. 무엇이 어긋나 있었나

```
infra origin/main  apps/dev/ai/values.yaml
  image.tag          4d667c3ee563…    ai main HEAD (2026-07-31 릴리스)
  bootstrap.version  preset-204824bd37e6    프리셋 개정 전 기준

ai origin/dev  d87c5f5    main 에 없는 커밋 10개
```

두 값이 각각 다른 이유로 낡아 있다. `image.tag` 는 정상 동작의 결과이고,
`bootstrap.version` 은 아무도 갱신하지 않는 필드다. 하나로 묶어 보면 원인을 찾을 수
없다.

---

## 1. `ai-image / publish` 는 왜 SKIPPED 인가 — **설계다**

### 1.1 어디에 있나

별도 워크플로가 아니다. `.github/workflows/ai-ci.yml` 의 **`image-publish` job** 이고,
`name:` 이 `ai-image / publish` 라서 CI 체크 목록에는 다른 워크플로처럼 보인다.

```yaml
# .github/workflows/ai-ci.yml:157
image-publish:
  name: ai-image / publish
  if: ${{ github.event_name == 'push' && github.ref == 'refs/heads/main' }}
  needs: check
```

`gh api .../actions/workflows` 로는 보이지 않는다. 그 목록은 워크플로 파일만 세고
job 은 세지 않기 때문이다. "워크플로 목록에 없다"에서 "존재하지 않는다"로 넘어간
것이 이 건의 첫 오판 지점이다.

### 1.2 왜 건너뛰나 — 조건 두 개가 모두 필요하다

| 이벤트 | `github.ref` | 결과 |
|---|---|---|
| `pull_request` | (PR ref) | SKIPPED — `event_name` 불일치 |
| `push` | `refs/heads/dev` | SKIPPED — `ref` 불일치 |
| `push` | `refs/heads/main` | **실행** |

PR 에서 항상 SKIPPED 였던 것도, `dev` push(run `30785007536`, 08-03 04:39Z)에서
SKIPPED 인 것도 같은 조건이 만든 두 사례다. 그 run 의 job 셋은 `check` success ·
`embedding profile parity` success · `ai-image / publish` **skipped** 였다.

PR 에서 이미지가 아예 안 만들어지는 것은 아니다. `check` job 이
`Validate AI container image` 스텝에서 `push: false` 로 빌드만 한다(`ai-ci.yml:113`).
빌드는 검증하고 게시는 하지 않는 것이 설계다.

### 1.3 설계라는 근거 넷

한 곳이 아니라 네 곳이 같은 말을 한다. 고장이면 네 곳이 같이 틀렸어야 한다.

| # | 근거 | 내용 |
|---|---|---|
| ① | `ai-ci.yml:154-156` 주석 | *"check 는 main·dev 양쪽에서 돌지만 publish 는 main 전용이다. infra 의 `ai-image-update.yaml` 이 `test "$SOURCE_BRANCH" = main` 으로 어서션하므로 이 조건을 dev 로 넓히면 GitOps 반영이 거부된다."* |
| ② | `tests/test_ci_image_publish_contract.py:48` | `test_publish_is_main_push_only_after_successful_ci_with_job_scoped_write` 가 두 조건을 **문자열로 고정**한다 — `assert "github.event_name == 'push'" in publish["if"]` · `assert "github.ref == 'refs/heads/main'" in publish["if"]`. 조건을 넓히면 CI 가 RED 다 |
| ③ | `infra .github/workflows/ai-image-update.yaml:44` | `test "$SOURCE_BRANCH" = main` — `vars.AI_SOURCE_BRANCH` 를 리터럴 `main` 과 대조한다. 이어서 `gh api "repos/Team-PinLog/ai/git/ref/heads/$SOURCE_BRANCH"` 로 **`main` 의 HEAD 만** 조회한다 |
| ④ | `CONTRIBUTING.md:48-50` | *"`main` 으로는 `dev` 를 릴리스 시점에 병합하며, 컨테이너 이미지 publish 는 `main` push 에서만 일어난다."* |

②가 특히 결정적이다. 이 조건은 주석이 아니라 테스트로 고정되어 있다. `dev` 로
넓히려면 계약 테스트를 먼저 고쳐야 하고, 그 테스트는 "왜 main 전용인가"를 이름에
적어 두었다.

### 1.4 그렇다면 dev 배포는 무엇을 쓰기로 돼 있었나 — **`ai` 의 `main`**

이름이 겹쳐서 오해를 부른다. 두 개의 "dev" 는 다른 것이다.

```
infra apps/dev/ai/       dev 클러스터(배포 환경 이름)
ai    origin/dev         통합 브랜치(레포 안의 브랜치 이름)
```

`apps/dev/ai/values.yaml` 이 pin 하는 것은 `ai` 레포 `main` 의 HEAD 커밋 이미지다.
`ai` 의 `dev` 브랜치는 배포 경로에 등장하지 않는다. `CONTRIBUTING.md` 가 이것을
"dev 2단 구조"(통합 `dev` / 배포 `main`)라고 부른다.

결론은 다음과 같다. 고장이 아니고, `ai` 레포 안에 고칠 것이 없다. `dev` 에 병합한
것이 배포에 반영되지 않은 이유는 파이프라인이 끊겨서가 아니라 릴리스
PR(`dev` → `main`)이 아직 열리지 않았기 때문이다. 고칠 것은 코드가 아니라 절차이고,
그 절차는 `CONTRIBUTING.md` 에 이미 있다(§3).

---

## 2. `ai-image-update` 가 success 인데 아무것도 안 바뀐 이유

자동화는 정확히 설계대로 동작했다. 그리고 동작을 막던 설정 문제는 오늘 새벽에
고쳐졌다.

### 2.1 오늘 실제로 한 번 동작했다

| infra `ai-image-update` run | 결과 |
|---|---|
| 2026-08-03 00:32Z | failure |
| 2026-08-03 00:37Z | success → **infra `#172` 생성** |
| 2026-08-03 01:18Z | success |
| 2026-08-03 04:53Z | success |

`infra#172` *"[verified] chore: update AI dev immutable image"* 가 **00:39:26Z 에 병합**됐다.
이때 `image.tag` 가 `d317f563`(07-29, `ai#42`)에서 `4d667c3`(07-31 릴리스)로 갱신됐다.

08-03 00:07 에 `back#122` 로 전달한 "자동화가 한 번도 동작한 적이 없다 — Actions
변수·Secret 이 등록돼 있지 않다"는 그 사이에 해소됐다. 지금 자동화는 정상 동작
중이다.

### 2.2 04:53 의 success 가 뜻하는 것

정상 동작 중인 자동화가 왜 아무것도 바꾸지 않았는지는 job 체인을 따라가면 나온다.

```
1 detect-source      ai main HEAD 조회 → 4d667c3
2 verify-source-ci   main 의 성공 push run head_sha == 4d667c3 → candidate=true
3 verify-image       provenance 대조 · GHCR digest 검증 → 통과
4 create-infra-pr    "RED candidate contract before values update"
                       values 가 이미 tag·digest 와 일치 → 테스트가 exit 0
                       → changed=false → PR 을 만들지 않고 종료
```

`create-infra-pr` 의 RED 스텝은 "values 가 아직 안 맞다"를 실패로 확인하는 자리다.
통과해 버리면 갱신할 것이 없다는 뜻이고, 워크플로는 성공으로 끝난다.

즉 success 는 "이미지를 갱신했다"가 아니라 "확인했고 갱신할 것이 없었다"였다.
`main` 이 07-31 이후 움직이지 않았으니 30분마다 같은 결론이 반복된다.

> 이 건의 성격을 요약하면, 성공 신호가 동작을 뜻하지 않는다는 것이다. 같은 함정이
> §5 에 하나 더 있다. `bootstrap.version` 은 아무 워크플로도 검사하지 않으므로 틀려도
> 아무 신호가 나지 않는다.

---

## 3. 미반영분 — 커밋 10개 · 티켓 9개

`git log --oneline origin/main..origin/dev` (기준 §0):

| 커밋 | 티켓 | 배포 경로 영향 |
|---|---|---|
| `d87c5f5` | `-266` 단어형 질의 컷 — `τ_abs` 질의 길이 분기 | **앱 코드** `config.py`·`search_service.py`. `ai#87` 이 이것으로 닫힌다 |
| `2ee6291` | `-228` 프리셋 `examples` 개정 | **데이터** `data/keyword_preset.yaml` → §4 의 봉인 값 |
| `13123ce` | `-255` 검색 재현율 판별 | 문서·도구 |
| `bdba79a` | `-230` 문서 색인 단방향 검사 | 문서·CI 도구 |
| `df016df` | `-229` DB 실패 응답 문구 분리 | **앱 코드** `search_service.py` 오류 본문 |
| `7a758d9` | `-224` 죽은 설정 키 전수조사 | 문서 |
| `501faf6` | `-227` GMS 멀티모달 재고 | 문서·프로브 |
| `2a36dcb` · `0c23498` | `-226` 문서 색인 정합 CI 이관 | CI 도구 |
| `af72033` | **`-205` GMS 오류 본문 로그 유출 차단** | **앱 코드** `app/client/` 전반 |

`-205`(`ai#78`)가 이 건의 지시 목록에서 빠져 있었다. `back#122` 07-31 12:18
코멘트에서 *"이 변경은 다음 릴리스 대상입니다. 방금 게시한 `4d667c3…` 에는 들어 있지
않습니다"* 라고 직접 예고한 항목이고, 게이트웨이 오류 본문이 다섯 곳의
로그·트레이스백으로 나가던 경로를 막는다. 자격 증명은 에코되지 않는다는 것이
실측으로 확인됐으므로(같은 코멘트) 급한 사안은 아니지만, 미반영 목록에서 빠뜨릴
항목은 아니다.

### 3.1 릴리스 트리거는 이미 충족돼 있다

`CONTRIBUTING.md:59-65` 는 셋 중 하나면 릴리스 PR 을 열라고 한다.

- 배포 경로에 영향을 주는 변경(앱 코드·설정·의존성)이 dev 에 들어갔을 때 — **해당**
  (`-266`·`-229`·`-205`)
- 그 자체로 시연·검증에 필요한 기능이 병합됐을 때 — **해당** (`-266` 이 `ai#87` 을
  닫는다)
- main 이 dev 보다 10커밋 이상 뒤처졌을 때 — 10커밋, 경계값

첫 조건은 이미 며칠 전부터 충족돼 있었다. 07-31 에 `main` 이 16커밋 뒤처진 사고를
겪고 규약을 넣었는데, 규약이 "언제 여는가"만 정하고 "누가 알아채는가"를 정하지
않았다. 10커밋 조건은 사람이 세야 하고, 세는 시점을 강제하는 장치가 없다.

---

## 4. 새 프리셋 봉인 값 — **`preset-ab321360b0df`**

### 4.1 산출 방식을 실측으로 확정했다

문서에 적힌 표현(*"27 presets 의 full preset SHA-256"*)이 파일 하나의 해시인지,
preset 27개를 정규화해 계산한 것인지 애매했다. 실측으로 판별했다.

`infra docs/ai-dev-prerequisites.md:137-139` 가 전체 64자를 적어 두었다.

```
승인 version 은 preset-204824bd37e6 이며 27 presets 의 full preset SHA-256
204824bd37e6e1f056f1636ec1bb86d2585994a8cdbfd99bb188096cfca04034 에서 파생됐다.
```

`ai` 의 `data/keyword_preset.yaml` 을 리비전별로 해시해 이 값과 맞춰 봤다.

| 리비전 | `data/keyword_preset.yaml` 의 SHA-256 |
|---|---|
| `de6e995` (= `origin/main` `4d667c3` 시점 내용) | `204824bd37e6e1f056f1636ec1bb86d2585994a8cdbfd99bb188096cfca04034` |
| `2ee6291` (`-228`, = `origin/dev` `d87c5f5` 시점 내용) | `ab321360b0df0a338c5329cdc02294122eeab670d8eaad852e451822c021095b` |

64자 전부가 일치한다. 앞 12자만 맞은 것이 아니므로 우연이 아니다.

산출 방식은 다음과 같이 확정된다. `data/keyword_preset.yaml` 파일 전체 바이트의
SHA-256 을 구하고, 그 hex 앞 12자를 `preset-` 뒤에 붙인다. YAML 파싱·정규화·항목
단위 계산은 하지 않는다.

*"27 presets 의"* 는 27개를 담은 그 파일의 라는 뜻이었다. preset 개수는 개정 전후 모두
27개로 같아(`grep -c '^  - id:'`) 개수 자체는 이 판정에 쓰이지 않는다.

### 4.2 새 값

```
data/keyword_preset.yaml @ origin/dev d87c5f5
  SHA-256   ab321360b0df0a338c5329cdc02294122eeab670d8eaad852e451822c021095b
  앞 12자    ab321360b0df

bootstrap.version   "preset-204824bd37e6"  →  "preset-ab321360b0df"
```

### 4.3 재현

```bash
git show origin/dev:data/keyword_preset.yaml | sha256sum
```

워킹트리 파일로 재도 같다.

```bash
sha256sum data/keyword_preset.yaml
```

이 계산은 OS 에 무관하다. `.gitattributes` 가 `*.yaml text eol=lf` 로 고정하고 있어 인덱스와 워킹트리가
둘 다 LF 다(`git ls-files --eol data/keyword_preset.yaml` → `i/lf w/lf`). 두 방법의 결과가
실제로 같음을 확인했다.

### 4.4 chart 제약을 통과한다

`infra charts/microservice/templates/bootstrap-job.yaml` 이 `bootstrap.version` 에 거는 제약 셋:

| 제약 | 위치 | 새 값 |
|---|---|---|
| 비어 있지 않을 것 | `:2-3` | 통과 |
| DNS label `^[a-z0-9]([-a-z0-9]*[a-z0-9])?$` | `:8-9` | 통과 — 소문자·숫자·하이픈, 양끝이 영숫자 |
| Job 이름 `{fullname}-bootstrap-{version}` ≤ 63자 | `:11-12` | 통과 — **새 값과 옛 값의 길이가 19자로 같다** |

### 4.5 이 값이 왜 갱신돼야 하는가 — 그리고 왜 아무도 안 하는가

`bootstrap.version` 은 Job 이름에 들어간다(`:17`). 값이 그대로면 이미지가 갱신돼도
bootstrap Job 의 정체성이 바뀌지 않는다.

그런데 이 필드는 어떤 자동화도 건드리지 않는다. `infra tools/update_ai_image.py` 는
`image.tag` · `image.digest` · `provenance.pinlog.io/image-source-sha` 셋만 고쳐
쓰고(`FIELD_RE` 가 `repository|tag|digest` 로 한정, `:20-22`), PR 의 `add-paths` 도
`apps/dev/ai/values.yaml` 한 파일이다. `bootstrap.enabled` 가 `true` 인지
확인만 하고(`:50-51`) `version` 은 읽지도 않는다.

즉 `image.tag` 는 자동으로 따라오지만 `bootstrap.version` 은 사람이 갱신해야 하고,
틀려도 어떤 검사도 실패하지 않는다. §2 의 함정이 여기서 한 번 더 나타난다. 이번에는
잘못된 성공 신호가 아니라 아무 신호도 없는 것이다.

> `-269`(preset_version 개정 경로)와 다른 층이다. 그쪽은 판정 결과 레코드에 기록되는
> 버전 값이고, 이쪽은 실제로 DB 에 적재되는 데이터의 배포 아티팩트 식별자다. 섞지
> 않는다.

---

## 5. 경계 — 무엇이 우리 몫이고 무엇이 아닌가

| 항목 | 소관 | 상태 |
|---|---|---|
| `ai-image / publish` 의 `if:` 조건 | AI — **고칠 것 없음(설계)** | §1. 넓히면 계약 테스트 RED 이고 infra 가 거부한다 |
| 릴리스 PR `dev` → `main` | **AI** | 트리거 충족(§3.1). 이 작업에서 연다 |
| 이미지 게시 | AI 워크플로(자동) | `main` push 시 `image-publish` 가 한다 |
| `image.tag` · `digest` 갱신 | **인프라**(자동) | `ai-image-update` 가 30분 주기로. 살아 있음(§2.1) |
| `bootstrap.version` 갱신 | **인프라**(수동) | 새 값 §4.2. **자동화 대상이 아니다** |
| `apps/prod/ai` 등록 · `PINLOG_AI_BASE_URL` | **인프라** | `back#122` 의 원래 항목. 이 건과 별개로 열려 있음 |

`CONTRIBUTING.md:75-76` 이 이 경계를 이미 적어 두었다. *"병합 후 이미지가 게시되면
`infra` 에 `values.yaml` 의 `image.tag` 갱신을 요청한다. 게시와 반영은 다른 일이고,
후자는 AI 파트 권한이 아니다."* 이 문서는 그 문장에 `bootstrap.version` 도 같은
쪽이라는 사실을 더한다.

---

## 6. 남은 문제

| | |
|---|---|
| **릴리스 시점을 세는 사람이 없다** | `CONTRIBUTING.md` 규약은 조건만 정하고 관측을 정하지 않는다. 07-31 사고 뒤 규약을 넣었는데 08-03 에 같은 구간이 다시 벌어졌다. 규약이 아니라 장치가 필요한 자리로 보이나, 이 문서는 판정만 남기고 제안하지 않는다 |
| **`bootstrap.version` 에 검사가 없다** | 프리셋 파일이 바뀌어도 봉인 값이 낡았다는 신호가 어디에서도 나지 않는다. 검사를 어느 레포에 두는가는 `infra` 소관이 걸려 있어 단독으로 정할 수 없다 |
| **DB 행 수준 provenance** | `2026-07-31-ticket-audit-96-77.md` 3-c 가 남긴 별건 그대로 — 배포 아티팩트는 식별되지만 "DB 의 이 행이 어느 커밋 YAML 에서 왔는가"는 여전히 없다 |
