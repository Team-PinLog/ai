# Mermaid 다이어그램을 브라우저 없이 문법 검증하기

- **날짜**: 2026-07-23
- **관련**: [architecture.md 구조도 커밋](https://github.com/Team-PinLog/ai) (`210f90c`), [reports/2026-07-23-architecture-diagrams.md](../reports/2026-07-23-architecture-diagrams.md)
- **레이어**: 문서 도구

## 목표

`architecture.md`에 Mermaid 다이어그램(flowchart·sequenceDiagram)을 추가하면서, **커밋 전에** 문법 오류를 잡고 싶었다. 렌더는 보통 브라우저(Mermaid live editor)에서 하지만, CLI 환경에서 자동 검증이 필요했다.

## 시도와 실패

- **`@mermaid-js/parser` 단독 사용 → 실패.** `parse()`가 `Unknown diagram type: flowchart`를 던진다. 이 패키지는 신형 문법 서브셋(pie·packet·architecture 등)만 파싱하고, `flowchart`/`sequenceDiagram`은 다루지 못한다.

## 해결

전체 `mermaid@11` + `jsdom`으로 `mermaid.parse()`를 돌린다. 브라우저 전역이 없어 두 가지를 보정해야 한다.

1. **`navigator`는 getter-only** — jsdom 위에서 `global.navigator = ...` 대입이 실패한다. `Object.defineProperty(global, 'navigator', { value: ... })`로 정의한다.
2. **CRLF** — Windows 체크아웃이라 코드펜스 추출 시 `\r\n`이 섞이면 파서가 흔들린다. 추출 후 `.replace(/\r\n/g, "\n")`로 정규화한다.

절차(스크래치 디렉터리에서):

```bash
mkdir mmv && cd mmv
npm init -y >/dev/null
npm i mermaid@11 jsdom >/dev/null
# validate.mjs:
#   - jsdom으로 window/document 구성, Object.defineProperty로 navigator 주입
#   - 대상 .md에서 ```mermaid ... ``` 블록 정규식 추출 + CRLF 정규화
#   - 각 블록을 await mermaid.parse(code) → 성공/실패 집계
node validate.mjs ../path/to/architecture.md
```

결과: `architecture.md`의 flowchart 3 + sequenceDiagram 1 = **4/4 문법 통과**. `linkStyle`, `classDef`, `alt/else`, 노드 shape 모두 유효.

## 재사용 메모

- 이 방식은 **문법(parse) 검증**이지 시각 렌더 확인이 아니다. 레이아웃·가독성은 별도로 봐야 한다.
- 새 다이어그램 추가 시 같은 스크립트로 회귀 검증 가능. GitHub는 `mermaid` 코드펜스를 자동 렌더하므로, parse만 통과하면 PR에서 그림으로 보인다.
