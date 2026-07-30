# P45. 공개 설정은 코드가 정본이고, 주입이 필수인 것은 비밀뿐이다

- 상태: Accepted
- 날짜: 2026-07-29
- 관련: `S15P11A705-96` · [P32](README.md) · [model-profile.md §2.1](../spec/model-profile.md) · [ai#32](https://github.com/Team-PinLog/ai/pull/32) · [ai#34](https://github.com/Team-PinLog/ai/pull/34)

## 무엇을 정하는가

설정값을 **비밀 / 공개** 둘로 가르고, 각각의 정본 위치를 다르게 둔다.

| | 정본 | 주입 | 없으면 |
|---|---|---|---|
| **비밀** | 배포 환경 | **필수** | 기동 실패 |
| **공개** | `app/core/config.py` 기본값 | 덮어쓰기(선택) | 기본값으로 동작 |

```
비밀   DATABASE_URL · GMS_API_KEY · INTERNAL_SHARED_SECRET
       GMS_BASE_URL — 값 자체는 비밀이 아니나 배포마다 달라 주입받는다

공개   PINLOG_EMBEDDING_MODEL · _DIMENSION · _DISTANCE · _PROFILE
       PINLOG_JUDGE_MODEL · KEYWORD_CANDIDATE_TOP_K · SIMILARITY_FLOOR · PROCESSING_EXPIRY_SEC
```

**공개 값 중 EMBEDDING 넷만 기본값이 없었다.** 나머지는 이미 코드 기본값을 갖고 있었으므로, 이 결정은 새 규칙을 만든 것이 아니라 **어긋나 있던 넷을 규칙에 맞춘 것**이다.

## 왜 — 원 결정을 뒤집는 근거

[model-profile.md §2.1](../spec/model-profile.md)은 원래 이렇게 적혀 있었다.

> 기본값을 코드에 넣지 않습니다. 값이 없으면 기동 실패입니다.
> 기본값이 있으면 배포 설정 누락이 조용한 Profile 불일치로 나타납니다.

### 그 논거는 "주입이 필수"라는 전제 위에 있다

주입이 필수일 때만 "누락"이 성립한다. 주입을 **덮어쓰기**로 돌리면 기본값으로 뜨는 것이 정상 동작이므로 누락이라는 상태 자체가 없어진다. 전제가 사라지면 결론도 사라진다.

### 반대로 원 결정의 대가가 실측됐다

값이 배포 설정에만 있으면 **교체가 git 이력도 리뷰도 남기지 않는다.**

`PINLOG_EMBEDDING_PROFILE` 변경은 [§3.2](../spec/model-profile.md)에 따라 **기존 임베딩을 전부 조회 대상에서 빼는** 결정이다. 그런데 그것이 GitHub Secret 콘솔 편집 한 번으로 가능했다 — PR 없이, 리뷰 없이, "누가 언제 왜 바꿨나"의 기록 없이.

**공개 값을 비밀처럼 다루면 보안은 늘지 않고 감시만 줄어든다.** 모델명·차원·거리함수·프로필 식별자는 이미 공개 문서 [P32](README.md)에 적혀 있어 숨겨진 적이 없다.

### 불일치 탐지는 그대로다

기본값을 둔다고 검사가 사라지지 않는다.

```
기동 시    _profile_consistency — profile 문자열이 model·dimension·distance 를 부분 문자열로
           포함하지 않으면 SettingsError. 덮어쓰기로 일부만 바꾸면 여기서 걸린다
런타임     §3.1 — Spring 이 보낸 embeddingProfile 이 서버 설정과 다르면 422 로 거부하고
           양쪽 값을 로그에 남긴다
```

원 논거가 걱정한 *"조용한 Profile 불일치"*는 이 둘이 막는다. 기본값의 유무와 무관하다.

## 기각한 대안

### `config/*.yaml` 계층 도입

`config/default.yaml` · `development.yaml` · `local.yaml`로 공개 설정을 분리하는 안. 공개/비공개 분리라는 목적은 같게 달성한다.

**기각 이유는 설정 소스가 셋이 되기 때문이다.** 지금은 `config.py` 기본값 → 환경변수 둘뿐이고 우선순위가 자명하다. YAML 레이어를 더하면 `yaml → env → Secret` 셋이 되고, 장애를 볼 때마다 *"지금 이 값이 어디서 왔나"*를 역추적해야 한다. 얻는 것(파일로 환경별 분리)보다 잃는 것(추적 가능성)이 크다.

`pydantic-settings`를 이미 쓰고 있어 **기본값을 필드에 적는 것만으로 같은 이익이 나온다** — 정본이 코드에 있고, 변경이 PR을 거치며, 환경변수로 덮을 수 있다.

### Secret 에 그대로 두기 (원 상태 유지)

Infra 가 주입 경로를 하나로 요구했으므로 그 요구를 따르는 안. **기각한 이유는 위 "원 결정의 대가"** 그대로다.

다만 이 기각은 Infra 요구를 거스르지 않는다 — 값이 이미지에 들어 있으므로 **주입할 것이 없어진다.** 실험·롤백으로 덮어써야 하면 ConfigMap 으로 넣으면 되고, 그 경로에 암호화가 필요 없다.

## 여파

| | 무엇이 바뀌나 |
|---|---|
| `app/core/config.py` | EMBEDDING 넷에 기본값. `alias` 는 유지되므로 환경변수 덮어쓰기 경로는 그대로 |
| `.env.example` | 비밀/공개 두 절로 나누고 각 절의 규칙을 명시 |
| `model-profile.md §2.1` | *"기본값을 넣지 않는다"* → *"기본값이 정본이다"*. 개정 사실과 근거를 인용문으로 남김 |
| `seal-runtime-secrets.yml` | 런타임 owner 값 **3종**만 봉인하고 Infra PR token은 action 인증에만 쓴다. EMBEDDING 넷은 주입하지 않는다 |
| GitHub Actions Secret | 등록된 EMBEDDING 넷은 **지우지 않아도 된다** — workflow 가 읽지 않을 뿐이다 |

**모델을 교체할 때**의 절차가 이렇게 바뀐다.

```
전    GitHub Secret 콘솔에서 4개 수정 → 재배포          이력·리뷰 없음
후    config.py 4줄 수정 → PR → 리뷰 → 머지 → 재배포    이력·리뷰 있음
```

## 감수하는 것

**Spring 과의 이중화가 남는다.** [§2](../spec/model-profile.md)의 "단일 정본"은 Spring 과 FastAPI 가 같은 값을 갖게 하려는 것인데, 코드 기본값을 두면 나중에 Spring 쪽에도 리터럴이 생길 수 있다.

현재는 문제가 되지 않는다 — **Spring 은 아직 `embeddingProfile` 을 다루지 않는다**(레포 전수 검색 0건). 검색 연동(`S15P11A705-135`)에서 붙을 때 §2.2 의 런타임 대조가 그 역할을 하지만, **어느 쪽이 정본인지는 그때 정해야 한다.** 지금 정하지 않는다 — 소비자가 없는 상태에서 정한 규칙은 붙일 때 다시 뒤집힌다.

**기본값이 낡을 수 있다.** 배포 환경이 실제로 다른 모델을 쓰는데 코드 기본값을 안 고치면, 주입을 잊었을 때 낡은 profile 로 뜬다. 이는 §3.1 이 런타임에 잡지만 그 전까지는 드러나지 않는다. **덮어쓰기를 상시로 쓰지 않는 것**이 이 결정의 전제다 — 상시 덮어쓰기가 필요해지면 그 값은 공개가 아니라 환경 종속이므로 분류를 다시 봐야 한다.
