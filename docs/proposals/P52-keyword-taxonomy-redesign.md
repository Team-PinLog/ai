# P52. Keyword taxonomy·visibility 재설계 — 공개 장소 속성과 사적 맥락의 분리

- **상태**: 제안 — 2026-08-07 사용자 채택 (실행은 별도 결정)
- **날짜**: 2026-08-07
- **관련**: P26(현행 27종 확정 — **이 제안 채택 시 supersede 대상**) · P47(라벨·축·불변 계약 — 채택 시 부분 개정 대상) · P51(프리셋 거버넌스 — 채택 시 §7 확장 전제 개정 대상) · P50(3계층 분리 — 정합) · 공용 계약 05 §8/07 ERD(채택 시 후속 개정 대상) · S15P11A705-292(표시명 명사형 통일 — 라벨 정본)
- **번호는 잠정 P52다.** 커밋 시점의 색인 기준으로 재확정한다.

## 1. 제안 요약

**문제.** 현행 Keyword Preset 27종의 visibility 분포는 PUBLIC 25 · PRIVATE_ONLY 2 · BLOCKED 0이다. 3계층과 소비 경계는 구현돼 있으나, 분포가 말하는 것은 「거의 모든 키워드가 공개 정보 취급」이라는 사실이다. 레코드·컬렉션을 공개하면 PUBLIC 키워드가 함께 노출되므로, 이는 **장소에 대한 공개 정보와 사용자의 사적 기억·관계·경험을 정보모델이 구분하지 못하는 문제**다.

**제안.** 27종 고정을 해제하고 taxonomy를 재설계한다.

1. **visibility의 의미 재정의**(§3)와 **Privacy 판정 계약**(§3.1)을 먼저 세우고, 기존 27종 전수를 정본 정의문(description·examples)까지 대조해 재평가한다(§5).
2. **category를 7축으로 재설계**한다(§4) — 현행 SITUATION의 과부하(장소 적합성·개인 사건·기억 혼재)를 해소한다.
3. 의미 영역(coverage) 공백에 신규 Preset을 도출한다(§6·§7). 목표 개수를 먼저 정하지 않는다.
4. 소비 경계는 현행보다 엄격하게 유지한다(§8).

**이 문서의 범위는 설계까지다.** 실행은 별도 결정이며, 조건·의존은 §9에 사실로만 기술한다.

**이 제안이 아닌 것.** 폐기된 -338(검색 회복용 명사 보강)의 부활이 아니다. 신규 도입 사유는 「장소를 기억할 때 반복적으로 필요한 맥락인데 현행이 표현하지 못한다」 하나만 허용한다. 검색어 표현 차이는 P49 질의 재작성의 영역이다.

## 2. 현재 문제 — 실측 근거

- **분포**: 활성 27종 중 PUBLIC 25(92.6%). PRIVATE_ONLY는 「동료」·「기념일」 2종뿐.
- **일관성 결함**: 동행 5종(친구·연인·가족·아이·혼자)이 PUBLIC — 장소가 아니라 사용자의 관계를 타인에게 알린다. 「동료」만 비공개인 배치는 의미 기준이 아니라 우연이다.
- **정의문과 등급의 부정합**: ACTIVITY 8종의 정본 정의는 전부 「~하는 **방문·활동·모임**」 — 사용자 행위의 기록으로 정의돼 있으면서 등급은 전부 PUBLIC이다(§5.1 전수 대조).
- **표현 공백**: 기억·경험 어휘와 물리 환경·운영 어휘가 각 0종.
- **표시명 정본 주의**: 이 문서의 라벨은 YAML 정본(-292 명사형 통일)을 따른다. 시연·스냅샷 DB에는 옛 표시명이 남아 있다 — §9의 정합 defect 참조.
- **추천 과다 노출은 별개 원인** — §10에서 분리 분석.

## 3. visibility의 의미 재정의 (판정 기준)

| 등급 | 의미 | 판정 질문 |
|---|---|---|
| PUBLIC | 주로 사용자가 아니라 **장소 자체**(속성·적합성·용도)를 설명하며, 타인이 봐도 사적 정보 노출 위험이 낮다 | 이 키워드가 어느 사용자의 기록에 붙었다는 사실을 타인이 봐도, 주로 그 장소의 성질에 대한 정보로 읽히는가? |
| PRIVATE_ONLY | 장소보다 **사용자의 관계·동행·행동·사건·기억**을 설명한다. 본인 회상·개인화에 가치가 크지만 타인 공개가 불필요하다 | 이 키워드가 붙었다는 사실이 그 사용자에 대한 정보(누구와·무엇을 했나·무슨 일·어떤 기억)로 읽히는가? |
| BLOCKED | 「더 사적」이 아니라 **정형 키워드로 추론·판정·축적하는 것 자체가 부적절한 민감 개념** | 이 개념을 구조화해 저장하는 것 자체가 적절한가? |

**행위 키워드의 경계 규칙.** PRIVATE 정의가 「행동」을 포함하므로 활동 계열은 다음 기준으로 가른다: 행위가 **장소의 용도와 사실상 1:1이고 개인 특정성이 미미한 보편 이용 행위**(끼니·후식·걷기·구매·관람)는 장소 설명으로 읽혀 PUBLIC이 성립한다. **정본 정의가 장소 적합성이 아니라 사용자의 특정 행위·모임·사건을 나타내면** PRIVATE_ONLY다. 판정 근거는 표시명이 아니라 **정본 description·examples**다(§5.1).

**category와 visibility는 독립 속성이다.** 같은 category 안에 PUBLIC과 PRIVATE_ONLY가 공존할 수 있다(예: ACTIVITY 안의 음주).

### 3.1 Privacy 판정 계약 (신설 — 판정기 규칙으로 편입)

PUBLIC 판정은 키워드 계열에 따라 두 규칙으로 나뉜다. §3의 경계 규칙과 정합하도록 요구 수준을 구분한 것이다.

> **규칙 1 — PUBLIC ACTIVITY(보편 이용 행위):** 개인 특정성이 낮은 보편 이용 행위(식사·산책·쇼핑 등)가 Context에 명시되면 판정할 수 있다. 행위의 명시로 충분하며, 별도의 장소 적합성 진술을 요구하지 않는다.
>
> **규칙 2 — PUBLIC 속성·적합성(ATMOSPHERE·FACILITY·SUITABILITY):** 사용자의 동행·행동·사건만으로 대응 PUBLIC 장소 적합성 Keyword를 추론하지 않는다. 장소의 속성·적합성이 Context에서 **명시적으로 진술된 경우에만** 판정한다.

규칙 2가 없으면 PRIVATE 어휘와 PUBLIC 어휘의 분리가 판정 단계에서 무너진다 — 동행 사실이 공개 적합성 키워드로 번역되어 노출되기 때문이다.

| 쌍 | 판정 가능 (명시적 진술) | 판정 금지 (추론) |
|---|---|---|
| WITH_KIDS ↔ KID_FRIENDLY | "키즈존이 있어서 아이가 놀기 좋다" → KID_FRIENDLY | "아이랑 다녀왔다" → WITH_KIDS만. 동반 사실로 KID_FRIENDLY를 붙이지 않는다 |
| ALONE ↔ SOLO_FRIENDLY | "1인석이 많아 혼자 가기 편하다" → SOLO_FRIENDLY | "혼자 가서 책 읽고 옴" → ALONE만 |
| GATHERING ↔ GROUP_FRIENDLY | "단체석이 넓어서 여럿이 가기 좋다" → GROUP_FRIENDLY | "동아리 모임을 했다" → GATHERING만 |

역방향도 같다 — 장소 적합성 진술("키즈존이 있다")만으로 동행 사실(WITH_KIDS)을 붙이지 않는다. 이 계약은 판정 프롬프트 규칙과 평가 하네스 케이스로 함께 고정해야 하며(P51 §12의 입력 변경 규율), 그 실측은 실행 단계 몫이다.

## 4. category 재설계 — 7축 확정안

현행 4축의 문제는 SITUATION이 장소 적합성·개인 사건·기억을 모두 떠안는 것이다. **이 제안은 7축 재편을 확정안으로 제시한다.**

| category | meta-domain | 의미 | 소속 (재평가·신규 반영) |
|---|---|---|---|
| ATMOSPHERE | PLACE_PROPERTY | 장소 분위기 | 조용함·아늑함·감성·활기·공간감·전망·복고·(신규)사진 명소 |
| FACILITY (신설) | PLACE_PROPERTY | **설비·운영 환경 — 장소가 갖춘 객관적 조건** (물리 설비와 운영 특성을 포함) | (신규)야외 좌석·루프탑·주차 편의·심야 영업 |
| SUITABILITY (신설) | PLACE_PROPERTY | 이용 적합성 — 「~하기(가기) 좋은」 판단 | 비 오는 날·짧은 방문·(신규)아이 동반·반려동물 동반·단체 적합·혼자 방문 적합 |
| ACTIVITY | PLACE_PROPERTY | 장소 용도로 읽히는 보편 이용 행위 | 대화·식사·디저트·학습·업무·산책·쇼핑·전시 관람 + 음주(PRIVATE — visibility 독립의 실례) |
| COMPANION | PERSONAL_CONTEXT | 동행·관계 | 친구·연인·가족·동료·아이·혼자 |
| OCCASION (신설) | PERSONAL_CONTEXT | 개인 사건·상황 | 기념일·단체 모임·축하·(신규)데이트·생일·여행·회식 |
| MEMORY (신설) | PERSONAL_CONTEXT | 기억·경험 | (신규)첫 방문·단골·재방문 의사·추억 |

- **FACILITY와 SUITABILITY의 경계**: FACILITY는 장소가 갖춘 것(설비·운영 시간 등 객관 조건 — 심야 영업 포함), SUITABILITY는 이용자 관점의 적합성 판단(「~하기 좋은」)이다. 심야 영업은 물리 설비는 아니지만 운영 특성이라는 객관 조건이므로 FACILITY 정의를 「설비·운영 환경」으로 확장해 수용한다 — SUITABILITY로 옮기는 대안은 「심야에 가기 좋다」는 판단으로 의미가 바뀌어 기각했다(§11).
- **SITUATION은 폐지된다** — 잔류 항목이 없다. meta-domain은 분석·검증용 상위 개념으로만 유지하고 DB category로 만들지 않는다.
- 이 재편은 계약 개정 대상(05 §8.2 「초기 범주 4종」·07 ERD 주석)이며, back의 category 테스트 리터럴·표시 순회 로직·id=축 순서 결박 파급이 §9에 있다. 판정 프롬프트에 category가 노출되므로 재편은 판정 품질 재실측을 동반한다.

## 5. B — 기존 27종 전수 재평가표

### 5.1 ACTIVITY 8종 — 정본 정의문 전수 대조

정본 description은 8종 전부 「~하는 방문/활동/모임」(행위)이다. §3 경계 규칙·§3.1 규칙 1을 적용한 결과:

| id | code | label | 정본 정의(요지) | 판정 |
|---|---|---|---|---|
| 201 | COFFEE_CHAT | 대화 | "카페에서 대화를 나누는 방문. 티타임·수다 포함" | **PUBLIC KEEP** — 상대·관계가 특정되지 않는 보편 이용 행위(규칙 1). code(COFFEE_CHAT)와 라벨(대화)의 의미 차는 「커피챗 ⊂ 대화」의 일반화라 정보모델 왜곡이 없고, code는 내부 식별자로 외부 미노출이므로 REPLACE 비용(재판정·이력 단절)을 정당화하지 못한다 — KEEP |
| 202 | MEAL | 식사 | "끼니를 해결하는 방문. 밥·점심·저녁 약속 포함" | **PUBLIC KEEP (정비 조건)** — 보편 이용 행위. 정의문의 「약속」(사건 어휘)은 OCCASION 영역이라 제거 정비 |
| 203 | DRINK | 음주 | "음주를 곁들인 **모임**. 한잔·반주·**회식** 포함" | **PRIVATE_ONLY MOVE** — **정본 정의가 장소 적합성이 아니라 사용자의 실제 음주 방문·모임을 나타낸다**(정보모델 기준). §3 경계 규칙의 「특정 행위·모임」에 해당한다. 부차적으로 음주 사실의 노출 민감성도 이 판정을 지지한다. 장소 유형(주점) 표현은 Place metadata 소관이라 공개 대응물을 신설하지 않는다 |
| 204 | DESSERT | 디저트 | "단것을 즐기는 방문" | **PUBLIC KEEP** — 보편 이용 행위·장소 용도 1:1 |
| 205 | STUDY_WORK | 학습·업무 | "공부나 노트북 작업을 하는 방문" | **PUBLIC KEEP** — 보편 이용 행위. 예문부터 장소 적합성 진술 중심이라 정합성이 가장 강하다 |
| 206 | WALK | 산책 | "걸으며 둘러보는 활동" | **PUBLIC KEEP** — 보편 이용 행위 |
| 207 | SHOPPING | 쇼핑 | "물건을 구경하거나 사는 방문" | **PUBLIC KEEP** — 동일 |
| 208 | EXHIBITION | 전시 관람 | "전시나 공연을 보는 방문" | **PUBLIC KEEP** — 동일 |

§3.1 규칙 1의 채택으로 v3의 「description을 장소 적합성 진술로 정비」 일괄 조건은 해소됐다 — 보편 이용 행위는 행위 명시로 판정 가능하다. 정비가 남는 것은 사건 어휘가 섞인 2건뿐이다(MEAL의 「약속」, DRINK의 「회식」 — 후자는 TEAM_DINNER 신설과 함께 OCCASION으로 분리).

### 5.2 유지 — 장소 속성·보편 활동 (PUBLIC, 16종)

| id | code | label | 현행 | 제안 | 처분 | 근거 |
|---|---|---|---|---|---|---|
| 301~307 | QUIET·COZY·TRENDY·LIVELY·SPACIOUS·VIEW_GOOD·RETRO | 조용함·아늑함·감성·활기·공간감·전망·복고 | ATMOSPHERE·PUBLIC | ATMOSPHERE·PUBLIC | KEEP | 장소 분위기·물리 속성 그 자체 |
| 201·202·204~208 | COFFEE_CHAT·MEAL·DESSERT·STUDY_WORK·WALK·SHOPPING·EXHIBITION | 대화·식사·디저트·학습·업무·산책·쇼핑·전시 관람 | ACTIVITY·PUBLIC | ACTIVITY·PUBLIC | KEEP | §5.1 (MEAL은 정의문 정비 조건) |
| 404 | RAINY_DAY | 비 오는 날 | SITUATION·PUBLIC | **SUITABILITY**·PUBLIC | MOVE(category) | 날씨 조건부 이용 적합성 |
| 405 | QUICK_STOP | 짧은 방문 | SITUATION·PUBLIC | **SUITABILITY**·PUBLIC | MOVE(category) | 이용 방식 적합성 |

### 5.3 이동·대체 — 사용자의 맥락 (PRIVATE_ONLY)

| id | code | label | 현행 | 제안 | 처분 | 근거 |
|---|---|---|---|---|---|---|
| 101 | WITH_FRIENDS | 친구 | COMPANION·PUBLIC | COMPANION·**PRIVATE_ONLY** | MOVE | 동행(사회적 관계) 정보. 장소 적합성은 GROUP_FRIENDLY(신규)가 맡는다 |
| 102 | WITH_PARTNER | 연인 | COMPANION·PUBLIC | COMPANION·**PRIVATE_ONLY** | MOVE | 연애 관계의 존재를 알리는 정보 — 동행 중 민감도 최고 |
| 103 | WITH_FAMILY | 가족 | COMPANION·PUBLIC | COMPANION·**PRIVATE_ONLY** | MOVE | 동행 사실은 개인 맥락 |
| 105 | WITH_KIDS | 아이 | COMPANION·PUBLIC | COMPANION·**PRIVATE_ONLY** | MOVE | 자녀의 존재 함의. KID_FRIENDLY와 §3.1 규칙 2로 분리 |
| 106 | ALONE | 혼자 | COMPANION·PUBLIC | COMPANION·**PRIVATE_ONLY** | MOVE | 행동 양식·생활 패턴 정보 |
| 104 | WITH_COLLEAGUES | 동료 | COMPANION·PRIVATE_ONLY | COMPANION·PRIVATE_ONLY | KEEP | 유지 — 단독 비공개이던 비일관성이 해소된다 |
| 203 | DRINK | 음주 | ACTIVITY·PUBLIC | ACTIVITY·**PRIVATE_ONLY** | MOVE | §5.1 — 정본 정의가 사용자의 음주 방문·모임을 나타낸다. category 유지(visibility 독립의 실례) |
| 401 | DATE_COURSE | 데이트 | SITUATION·PUBLIC | (비활성) | **REPLACE** | **§4 처분 어휘의 대표 적용 사례.** code(DATE_COURSE)는 「데이트 코스」라는 장소 적합성 의미인데 라벨·정의(데이트)는 개인 사건이다 — MOVE로 OCCASION·PRIVATE에 두면 code-semantic 불일치가 영구 잔존한다(code 불변 계약). 그래서 **DATE_COURSE를 DEACTIVATE하고 사건 의미의 신규 code(TBD-OCC-04 DATING·데이트·OCCASION·PRIVATE_ONLY)를 신설**한다. 기존 판정 이력은 행으로 보존되고 재판정 시 신규 code로 수렴한다 |
| 403 | GATHERING | 단체 모임 | SITUATION·PUBLIC | **OCCASION**·**PRIVATE_ONLY** | MOVE | 「모임을 했다」는 사건. code(GATHERING)도 모임 의미라 code-semantic 정합 — REPLACE 불요 |
| 406 | CELEBRATION | 축하 | SITUATION·PUBLIC | **OCCASION**·**PRIVATE_ONLY** | MOVE | 「축하할 일이 있었다」는 사건. code 정합 — REPLACE 불요 |
| 402 | ANNIVERSARY | 기념일 | SITUATION·PRIVATE_ONLY | **OCCASION**·PRIVATE_ONLY | MOVE(category) | visibility 유지, 재편 축으로 이동 |

**처분 결과 요약**: REPLACE 1건(DATE_COURSE → 신규 DATING) · DEACTIVATE는 그 REPLACE의 구성 요소로 1건 · LOGICAL_MERGE 0건. v2~v3의 「DEACTIVATE·REPLACE 해당 없음」 결론은 code-semantic 정합 검토(이번 보정)로 **철회·정정**한다 — 라벨 개정(-292)이 code와 의미를 갈라놓은 항목이 1건 있었고, 그것이 처분 어휘가 실제로 필요한 이유다. COFFEE_CHAT은 같은 관점에서 검토했고 KEEP이다(§5.1).

**재평가 결과 분포(기존 27종): 활성 26종 = PUBLIC 16 · PRIVATE_ONLY 10, 비활성 1종(DATE_COURSE).**

## 6. C — Coverage gap (meta-domain 분석)

PLACE_PROPERTY / PERSONAL_CONTEXT는 **coverage 분석용 meta-domain**이다(§4에서 7축과의 대응 확정, DB 값 아님).

| meta-domain | 하위 영역 | 현행 | 공백 → 신규 후보 근거 (기억 구조화 관점) |
|---|---|---|---|
| PLACE_PROPERTY | atmosphere | 7종 충족 | 사진 명소 1종 보강 — 장소 회상의 반복 축 |
| PLACE_PROPERTY | physical/operational environment | **0종** | 야외 좌석·루프탑·주차 편의·심야 영업 — 「루프탑이 좋았지」·「주차 편했던 곳」·「늦게까지 하는 곳」은 반복 회상 축. 도입 사유는 검색이 아니라 표현 공백 |
| PLACE_PROPERTY | suitability | 비 오는 날·짧은 방문(이동) | 아이·반려동물·단체·혼자 적합 — **동행 사실(개인) ↔ 장소 적합성(공개) 분리가 핵심 패턴**, §3.1 규칙 2가 경계를 고정 |
| PLACE_PROPERTY | activity | 8종 충족(음주는 PRIVATE 이동) | — |
| PERSONAL_CONTEXT | companion | 6종(전부 PRIVATE) | — |
| PERSONAL_CONTEXT | occasion | 기념일·단체 모임·축하(+데이트는 REPLACE로 신규) | 생일·여행·회식 — 「언제·무슨 일의 기억인가」 축 |
| PERSONAL_CONTEXT | memory | **0종** | 첫 방문·단골·재방문 의사·추억 — 가장 자연스러운 회상 축 |
| PERSONAL_CONTEXT | relationship | WITH_* 부분 충족 | 소개팅류는 관계 추론 민감도로 보류(§11) — 도입 시 PRIVATE_ONLY 필수 |

**BLOCKED 검토**: 의료·정신건강·법률·정치·종교 등 민감 개념은 **목록 미수록이 1차 방어**다. BLOCKED는 「배포된 code의 구조화 중단」·「발견된 민감 후보의 명시적 차단 기록」에 쓰는 상태다. **이번 안에서 0종인 이유는 신규안에 민감 추론 개념을 넣지 않았기 때문**이며, P51 발견 채널에서 민감 개념이 반복 출현하면 그때 BLOCKED 지정으로 이력을 남긴다.

## 7. D — 새 taxonomy 초안 전체안

**신규 id는 numeric을 부여하지 않는다** — 논리 임시 ID(TBD-*)로 표기하며, 실제 numeric ID는 back의 「id 자릿수=축 순서」 결박 해소 방침과 함께 확정한다. 기존 id는 재번호화하지 않는다.

### 신규 — PLACE_PROPERTY 계열 (PUBLIC 9종)

| 임시 ID | code | label | category | visibility | description(요지) |
|---|---|---|---|---|---|
| TBD-FAC-01 | OUTDOOR_SEATING | 야외 좌석 | FACILITY | PUBLIC | 테라스·야외 자리가 있는 장소 |
| TBD-FAC-02 | ROOFTOP | 루프탑 | FACILITY | PUBLIC | 루프탑·옥상 공간이 있는 장소 |
| TBD-FAC-03 | PARKING_OK | 주차 편의 | FACILITY | PUBLIC | 주차가 편한 장소 |
| TBD-FAC-04 | LATE_NIGHT | 심야 영업 | FACILITY | PUBLIC | 늦은 시간까지 여는 장소 — 운영 환경(§4 FACILITY 정의 확장의 근거 항목) |
| TBD-ATM-01 | PHOTO_SPOT | 사진 명소 | ATMOSPHERE | PUBLIC | 사진 찍기 좋은 장소 |
| TBD-SUIT-01 | KID_FRIENDLY | 아이 동반 | SUITABILITY | PUBLIC | 아이와 가기 좋은 설비·환경이 진술된 장소 (§3.1 규칙 2) |
| TBD-SUIT-02 | PET_FRIENDLY | 반려동물 동반 | SUITABILITY | PUBLIC | 반려동물 동반 가능이 진술된 장소 |
| TBD-SUIT-03 | GROUP_FRIENDLY | 단체 적합 | SUITABILITY | PUBLIC | 여럿이 가기 좋은 환경이 진술된 장소 |
| TBD-SUIT-04 | SOLO_FRIENDLY | 혼자 방문 적합 | SUITABILITY | PUBLIC | 혼자 가기 편한 환경이 진술된 장소 |

「대화하기 좋은」은 COFFEE_CHAT(대화)이 담당하므로 신설하지 않는다.

### 신규 — PERSONAL_CONTEXT 계열 (PRIVATE_ONLY 8종)

| 임시 ID | code | label | category | visibility | description(요지) |
|---|---|---|---|---|---|
| TBD-OCC-04 | DATING | 데이트 | OCCASION | PRIVATE_ONLY | 데이트로 간 곳이라는 사건 기억 — **DATE_COURSE의 REPLACE 대체 code**(§5.3) |
| TBD-OCC-01 | BIRTHDAY | 생일 | OCCASION | PRIVATE_ONLY | 생일에 간 곳이라는 사건 기억 |
| TBD-OCC-02 | ON_TRIP | 여행 | OCCASION | PRIVATE_ONLY | 여행 중 들른 곳이라는 사건 기억 |
| TBD-OCC-03 | TEAM_DINNER | 회식 | OCCASION | PRIVATE_ONLY | 회식 자리라는 사건 기억 (DRINK 정의의 회식 어휘 분리 전제 — §5.1) |
| TBD-MEM-01 | FIRST_VISIT | 첫 방문 | MEMORY | PRIVATE_ONLY | 처음 가 본 곳이라는 기억 |
| TBD-MEM-02 | REGULAR_SPOT | 단골 | MEMORY | PRIVATE_ONLY | 자주 가는 곳이라는 기억 |
| TBD-MEM-03 | WANT_REVISIT | 재방문 의사 | MEMORY | PRIVATE_ONLY | 다시 가고 싶은 곳 |
| TBD-MEM-04 | MEMORABLE | 추억 | MEMORY | PRIVATE_ONLY | 개인적으로 의미 있는 장소 |

### 결과 요약

- **활성 43종** = 기존 활성 26(DATE_COURSE 비활성 제외) + 신규 17. 분포: **PUBLIC 25 · PRIVATE_ONLY 18 · BLOCKED 0.**
- **개수와 P51의 관계**: 43은 P51 §7-3의 절대 가드 범위(40~60) 안이지만, **40 초과는 §7 사전조건의 적용 대상**이다 — 후보 검색 방식 개편 실측(§7-1)·Candidate Recall@K와 Judge Accuracy 지표 분리(§7-2)가 선행돼야 하며, **사전조건 충족 또는 P52 채택에 따른 해당 조항 개정 전에는 이 안의 실행 가능성을 전제하지 않는다.** 개수는 목표가 아니라 coverage의 결과다.
- 라벨은 정본 규칙(명사형, -292)을 따른다.

## 8. 소비 경계 원칙 — 확장 후에도 현행보다 엄격하게

| 소비 지점 | 사용 등급 | 현행 구현과의 관계 |
|---|---|---|
| 본인 기록·검색·회상 | PUBLIC + PRIVATE_ONLY | 현행 화이트리스트 그대로 |
| 본인 개인화(추천받는 쪽 Profile 신호) | PUBLIC + PRIVATE_ONLY (**외부 노출 금지**) | 현행 FeedKeywordRepository Profile 경로와 일치 |
| 타인의 레코드·컬렉션 표시 | PUBLIC only | 현행 화이트리스트 그대로 |
| 타인 컬렉션 추천의 콘텐츠 신호 | PUBLIC only | 현행 특징 경로와 일치 |
| BLOCKED | 판정·저장·검색·추천 전부 불사용 | 적재 제외 + 화이트리스트 밖 |

**PRIVATE_ONLY가 타인에게 노출되거나 타인 프로파일링에 쓰이는 것을 금지한다.** §3.1이 저장(판정) 단계의 경계를, 화이트리스트(fail-closed)가 조회 단계의 경계를 맡는다. 계약 예시 SQL 결함 1건(06 §5.5)은 정정 완료(docs `f5aa439`).

## 9. E — 영향 범위 (실행 시 변경 대상, 시점 판단 없음)

### 정합 defect — P52와 별개로 식별 (권고: 독립 defect 티켓)

**Defect: -292 표시명 개정이 시연·스냅샷 DB에 미반영, preset 임베딩·측정 자산이 YAML 정본과 어긋남.** 두 DB의 keyword_preset은 옛 표시명 시딩분이고, preset 임베딩은 display_name을 입력에 포함하므로 현행 keyword 측정 자산(keyword_matrix.json)·재정렬 채택값(-339)·게이트 검증은 옛 표시명 임베딩 위의 측정이다. 정본 재시딩 시 임베딩이 바뀌지만 preset_version이 항상 1이라 신선도 가드가 잡지 못한다 — **선결 결함 1(version 경로 부재)이 실제로 발생시킨 사례**다. P52 실행 여부와 무관하게 존재하는 정합 결함이므로 별도 티켓 추적을 권고한다(처리 방침·시점은 사용자 결정).

### 실행의 전제 조건 — 선결 결함 3건 (전부 미해결)

1. **preset_version 증가 경로 부재** — 신선도 가드 전체 불발(위 defect가 실증). 기존 티켓 S15P11A705-269.
2. **YAML 삭제 항목 미처리** — 시딩이 UPSERT만 수행. DEACTIVATE(REPLACE 포함)를 실행하려면 is_active 동기화 로직이 선행돼야 한다.
3. **재판정 수단 부재**(P47 §4) — 상태 되돌림·재스캔 수집·속도 조절 전부 없음. REPLACE(DATE_COURSE→DATING)의 「재판정 시 신규 code 수렴」도 이 수단에 의존한다.

### 레포별 변경 대상

- **ai**: YAML 개정(신규 17 + visibility 8건 MOVE + category 재편 + DATE_COURSE 비활성) → 전 항목 재임베딩 시딩 → §3.1 두 규칙의 판정 프롬프트 편입 + 평가 하네스 비교(P51 §12) → K=10·floor 0.30 재검토(모집단 26→43) → -339 채택값 재측정 → artifact 전면 재생성·라벨 재라벨링·도구 하드코딩 갱신(`demo_seed/verify.py` PRIVATE code 집합 2→18 필수 갱신 포함).
- **back**: id=축 순서 결박 해소 방침(신규 numeric ID 확정의 선행 조건). 7축 재편에 따른 표시 순회 로직·category 테스트 리터럴 갱신. visibility 등급 신설 없음 — CHECK 마이그레이션 불요. category는 DB CHECK 없어 DDL 불요, ERD 주석 개정.
- **docs(계약)**: 25~30 개수 조항(43은 범위 밖 — 개정 필수) · 「초기 범주 4종」 조항 + ERD 주석(7축) · **신설**: 처분 상태 어휘(§4)·§3.1 Privacy 판정 계약(2규칙)·preset_version 승격·재판정 규칙.
- **DB·seed**: 스냅샷·시연 재시딩(위 defect 처리와 병행 시 재시딩 횟수 절감 — 사실만 기록). 시연 재시딩은 라벨 시트 전량 무효(기지 함정).
- **tests**: 개수 비결박(자체 픽스처) — taxonomy로 깨지는 테스트 없음. 갱신 대상은 도구·리터럴.
- **front**: **taxonomy 직접 하드코딩·계약 결박 없음**(display_name 배열만 소비). Preset 증가에 따른 태그 표시량·layout·payload 영향은 **실행 전 별도 검증 항목**.

## 10. F — 추천 과다 노출 별도 분석 (taxonomy와 원인이 다름)

**관측은 정확하며, 버그가 아니라 설계가 그렇게 돼 있다.** 원인 4건: ① 후보 자격에 관련성 조건 0(`FeedCandidateRepository` — 공개·비삭제·비어있지 않음·타인·소유자 미탈퇴뿐) ② 소규모 데이터에서 recent 100+random 20이 전체 스캔 ③ Top-N 절단 부재(`FeedRanker.arrange`가 전량 재배치, 커서가 풀 소진까지 페이징) ④ 무관 후보도 recency 항만으로 항상 양수. 명세에도 임계·기권 개념이 없다 — 코드는 명세를 정확히 구현했다.

**개선 구조 스케치**(별도 작업 단위 권고): 전체 공개 컬렉션 → 관련성 후보 필터 → PUBLIC 신호 점수 → 최소 추천 기준 → 기준 미달 제거(기권 허용) → 추천. 「점수가 낮아도 순위에 넣는 것」과 「근거 없으면 추천하지 않는 것」의 구분이 핵심.

**도입 시 선행 개정**: `feed-recommendation.md:29`(키워드 없는 컬렉션 노출 원칙)·`FeedApiTests:123`(같은 계약 테스트)·계약 공백 4건(eligibility 임계·적합도 정의 위임·confidence 소비·preset_version 규칙). **taxonomy 개편과 원인·해법이 달라 별도 이슈로 유지한다.**

## 11. 채택하지 않은 대안 / 미결

- **물리 삭제·재번호화** — FK·code 불변 계약과 충돌, §4 처분 어휘로 대체.
- **meta-domain의 DB category화** — 분석용 상위 개념으로만 유지(§4).
- **DRINK의 PUBLIC 유지(정의 개정 방식)** — 「술 마시기 좋은 곳」으로 정의를 고치는 대안이 있으나, 그것은 사실상 다른 키워드를 만드는 것이고 현 정의(사용자의 음주 방문·모임)의 판정 이력과 단절된다. 정보모델 기준(정의가 나타내는 것)에 따라 MOVE가 정확하다고 판정.
- **DATE_COURSE의 MOVE 유지** — code-semantic 불일치가 영구 잔존해 기각. REPLACE 채택(§5.3).
- **LATE_NIGHT의 SUITABILITY 배치** — 「심야에 가기 좋다」는 적합성 판단으로 의미가 바뀜. 운영 특성은 객관 조건이므로 FACILITY 정의 확장으로 수용(§4).
- **미결**: 실행 시점·단계 구성(사용자 결정) / back id=축 순서 결박 해소 방침 / 「데이트 코스(장소 적합성)」 의미의 공개 키워드 신설 여부(수요 확인 후) / 관계 특정 어휘(소개팅류) / front 표시량·payload 검증 / 본인 화면 PRIVATE_ONLY 시각 구분(05-1 §1.4) / 정합 defect의 티켓화·처리 방침 / 추천 개선 과제의 이슈화.

## 12. 판단 기준 충족 확인

- **PUBLIC** — 장소 설명 표현력: 분위기 8 + 활동 7 + 적합성 6 + 설비·운영 4 = 25종. 속성·적합성 계열은 §3.1 규칙 2(명시적 진술), 활동 계열은 규칙 1(보편 행위 명시)로 판정.
- **PRIVATE_ONLY** — 사적 회상 표현력: 동행 6 + 행위 1(음주) + 사건 7 + 기억 4 = 18종, 전부 타인 비노출.
- **BLOCKED** — 경계 정의: 0종이되 「목록 미수록이 1차 방어, BLOCKED는 차단 이력 상태」로 설명(§6).
- **추천** — 공개됐다는 이유만의 추천을 막는 구조: 원인 분리 규명 + 개선 구조·선행 개정 목록(§10).
