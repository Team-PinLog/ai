# SealedSecret handoff — AI 소유 값을 값 노출 없이 클러스터로 넘긴다

`S15P11A705-96` · [ai#32](https://github.com/Team-PinLog/ai/pull/32) Infra 요청 ①

## 무엇을 푸는가

**GitHub Actions Secret은 Kubernetes Pod에 자동으로 전달되지 않는다.** 값을 클러스터까지 보내려면 누군가 한 번은 평문을 만져야 하는데, Infra는 *"key/token 값과 앱 호환성 설계를 AI owner 소유로 두고, 값을 열람하지 않은 채 암호화 Secret과 컨테이너만 배포하겠다"*는 경계를 세웠다.

SealedSecret이 그 경계를 성립시킨다. controller의 **공개키로 암호화한 manifest**만 넘기므로 Infra는 평문을 볼 수 없고, 복호화는 클러스터 안의 controller만 할 수 있다.

이 리포트는 그 산출 경로(`.github/workflows/seal-ai-secrets.yml`)의 구현 기록이다.

## 봉인 대상 7종

| 키 | 성격 | 앱에서 |
|---|---|---|
| `GMS_API_KEY` | 비밀 | `config.py` 필수 |
| `GMS_BASE_URL` | 준비밀(엔드포인트) | 필수 · `/gmsapi/` 형식 검증 |
| `INTERNAL_SHARED_SECRET` | 비밀 | 필수 · `X-Internal-Secret` 헤더 |
| `PINLOG_EMBEDDING_MODEL` | **비밀 아님** | 필수 · 기본값 없음 |
| `PINLOG_EMBEDDING_DIMENSION` | **비밀 아님** | 필수 |
| `PINLOG_EMBEDDING_DISTANCE` | **비밀 아님** | 필수 |
| `PINLOG_EMBEDDING_PROFILE` | **비밀 아님** | 필수 · 나머지 셋을 부분 문자열로 포함해야 함 |

**EMBEDDING 넷은 비밀이 아니다.** 모델명·차원·거리함수·프로필 식별자이고 정본은 공개 문서 [P32](../proposals/README.md)에 있다. `ai#32`에서 Infra도 이 넷을 *"non-secret ConfigMap 명시 주입"*으로 분류했다.

그럼에도 Secret 경로로 다루는 것은 **Infra가 주입 경로를 하나로 요구했기 때문**이다. 값이 공개라는 사실과 "어디서 주입되는가"는 별개이고, 앱이 이 넷에 기본값을 두지 않으므로(`app/core/config.py`) 누락되면 Pod이 아예 뜨지 않는다 — 경로를 나누면 그 실패가 두 곳에서 날 수 있다.

`DATABASE_URL`은 여기 없다. Infra가 별도 `ai-db-credentials`로 관리하고 `envFrom`으로 함께 연결한다.

## 설계 결정

### 평문 YAML을 만들지 않는다 — `kubeseal --raw`

흔한 경로는 `kubectl create secret generic --dry-run=client -o yaml | kubeseal`인데, 이 파이프의 **중간 산출물이 평문 base64 Secret YAML**이다. 파이프 안에만 있다 해도 한 단계 실수(리다이렉트, `tee`, 디버깅용 `cat`)로 파일이 된다.

`--raw`는 값 하나를 받아 암호문 한 줄을 돌려준다. 평문은 셸 변수와 파이프에만 존재하고 파일·인자·로그 어디에도 남지 않는다. 변수 확장은 셸 내부에서 끝나므로 `ps`로도 보이지 않는다.

대신 SealedSecret manifest를 손으로 조립해야 한다. `echo`로 쌓는다 — **heredoc은 쓸 수 없다.** 이 스크립트는 workflow YAML의 블록 스칼라(`run: |`) 안이라 들여쓰기가 곧 블록의 경계다. heredoc 본문을 왼쪽 끝에 붙이면 workflow YAML 자체가 끊기고, 들여쓴 채 두면 그 공백이 산출 파일에 들어가 SealedSecret이 깨진다. 실제로 둘 다 겪고서 `echo` 조립으로 바꿨다.

### 배포 전에 실패시킬 수 있는 것은 배포 전에 실패시킨다

앱이 기동 시 fail-fast로 거르는 검사를 **봉인 시점에 미리** 돌린다. 그러지 않으면 배포하고 Pod이 죽어야 알게 된다.

```
GMS_BASE_URL                 /gmsapi/ 포함        (embedding·judge 양쪽이 요구)
PINLOG_EMBEDDING_DIMENSION   정수
PINLOG_EMBEDDING_PROFILE     나머지 셋을 부분 문자열로 포함
                             (app/core/config.py 의 profile 정합 검사와 같은 규칙)
```

profile이 어긋난 채 배포되면 **기존 임베딩이 전부 조회 대상에서 빠진다** — 조용히 검색 결과가 비는 종류의 실패라 배포 후에 알아채기 가장 어렵다.

### 인증서를 지문으로 고정한다

`deploy/sealed-secrets/pinlog-dev-cert.pem`은 Infra가 `kube-system/sealed-secrets-controller`에서 가져다 준 공개 인증서다. 공개키라 레포에 커밋해도 안전하다.

workflow가 매번 SHA-256 지문을 대조한다(`4A:C5:…:A2`). **엉뚱한 공개키로 봉인하면 controller가 복호화하지 못하고, 그 실패는 배포 시점에야 드러나기 때문**이다. 만료 검사도 함께 한다(현재 유효기간 2026-07-20 ~ 2036-07-17).

### 산출물을 두 방향으로 검증한다

1. **모든 `encryptedData` 값이 `Ag`로 시작하는 100자 이상 base64인가** — 봉인이 빈 문자열이나 평문을 흘렸다면 여기서 걸린다
2. **평문이 그대로 새지 않았는가** — 단 **12자 이상만 본다.** `1536`·`cosine` 같은 짧은 값은 base64 암호문에 우연히 포함될 수 있어 거짓 양성을 만든다. 그 길이대는 (1)이 이미 덮는다

### scope는 strict

암호문이 `pinlog-dev/ai-owner-secrets`라는 **이름·네임스페이스 조합에 묶인다.** 다른 이름으로 옮겨 쓰려면 다시 봉인해야 한다. 봉인된 값이 엉뚱한 리소스로 복사되는 것을 controller가 거부하므로, 유출된 manifest 하나가 다른 네임스페이스에서 복호화되는 경로가 없다.

## 검증

| 항목 | 결과 |
|---|---|
| 인증서 지문 대조 | `openssl x509 -noout -fingerprint -sha256` — Infra 제공 값과 **일치** |
| 인증서 유효기간 | `-checkend 0` 통과 (2036-07-17까지) |
| kubeseal 릴리스·asset 이름 | `v0.27.1` 및 `kubeseal-0.27.1-linux-amd64.tar.gz` · `sealed-secrets_0.27.1_checksums.txt` **실재 확인** |
| workflow YAML 파싱 | 통과 (7 steps) |
| 셸 블록 문법 | `bash -n` 5개 전부 통과 |
| **실제 실행** | **미실행** — Actions Secret 등록 후에만 가능하다 |

**마지막 줄이 이 리포트의 한계다.** 봉인이 실제로 controller가 복호화할 수 있는 암호문을 만드는지는 **Infra가 클러스터에 적용해 봐야** 확정된다. 지문·형식·문법까지가 여기서 확인 가능한 범위다.

## 미결

- **`PINLOG_AI_INFRA_PR_TOKEN`** — Infra가 요구 목록에 넣었으나 **이 앱이 읽지 않는다**(`ai` 레포 전체 참조 0건). GitOps 레포에 PR을 여는 용도로 보이며, 그렇다면 전달 방식이 artifact가 아니라 PR이 된다. 대상 레포·권한 범위·만료·커밋 경로를 확인한 뒤 workflow를 그쪽으로 바꾼다
- **회전 절차** — 지금은 값을 갱신하고 workflow를 다시 돌리면 새 manifest가 나온다. 클러스터의 기존 Secret을 언제 어떻게 교체할지는 Infra 몫이고 합의되지 않았다
- **prod** — 이 인증서는 `pinlog-dev` 전용이다. prod는 별도 controller·인증서가 필요하다
