# Keyword 매칭 평가 하네스

Preset(`../../data/keyword_preset.yaml`)이 실제 Context를 얼마나 잘 덮고, LLM 판정이
안정적인지 임베딩·LLM으로 측정합니다. FastAPI·DB 없이 스크립트만으로 돕니다.

## 준비

```bash
pip install -r requirements.txt
```

키는 **환경변수** 또는 이 디렉터리의 `.env`(gitignore)로 주입합니다. **채팅·커밋에 키 값을 넣지 마세요.**

```
# .env 예시 (커밋 금지)
GMS_API_KEY=...
GMS_BASE_URL=https://.../v1          # OpenAI 호환 엔드포인트. 미지정 시 api.openai.com
PINLOG_EMBEDDING_MODEL=text-embedding-3-small
PINLOG_EMBEDDING_PROFILE=openai-text-embedding-3-small-1536-cosine-v1
ANTHROPIC_API_KEY=...                # 테스트 C 판정 LLM
PINLOG_JUDGE_MODEL=claude-sonnet-5
```

연결 확인: `python embed.py`

## 실행

```bash
python test_a_selfdup.py                        # 프리셋 자기 중복 (임베딩 27회)
python test_b_coverage.py --k 10 --floor 0.30   # 커버리지 (샘플 임베딩)

# 테스트 C — 2단계
python test_c_judge.py --provider anthropic --model claude-haiku-4-5   # C-1 프롬프트 안정화
python test_c_judge.py --compare                                       # C-2 모델 비교(GMS, MODELS 목록)
```

판정 모델은 계약상 미확정입니다. C-1은 접근 편한 모델 1개로 프롬프트를 잡고(세션 Claude 무방),
C-2는 확정 프롬프트로 GMS 후보 모델을 비교해 판정 모델을 정합니다. `test_c_judge.py`의 `MODELS`
목록을 GMS 실제 모델명으로 맞추세요.

임베딩 결과는 `.cache/`에 캐시되어 재호출을 막습니다.

## 구성

| 파일 | 내용 |
|---|---|
| `embed.py` | 임베딩 클라이언트·cosine·프리셋 로더 |
| `test_a_selfdup.py` | 프리셋 쌍별 cosine, ≥임계 병합 후보 |
| `test_b_coverage.py` | 미매칭율·쏠림도·사각지대·Recall@K·하한 제안 |
| `test_c_judge.py` | top-K 후보 → LLM 구조화 판정, 위반·과잉/과소 점검 |
| `prompts/keyword_judgment.md` | `/context/process` LLM 프롬프트·스키마 초안 (테스트 C 산출물) |
| `samples.yaml` | **임시** 샘플 맥락(내 작성, self-ref 편향 → 참고용). 팀 샘플로 교체 |
| `REPORT.md` | 실행 후 결과·프리셋 보정 권고 (생성물) |

## 주의

- `samples.yaml`은 프리셋을 알고 쓴 임시 데이터라 커버리지 수치는 **경향**으로만 봅니다.
  팀이 프리셋 안 보고 쓴 30~50개로 교체하면 유효한 측정이 됩니다.
- 테스트 C의 판정 LLM 모델은 계약상 미고정입니다. 프롬프트는 모델 이식성을 유지합니다.
- 프리셋 보정이 필요하면 `data/keyword_preset.yaml`을 고치고 별도 PR로 올립니다.
