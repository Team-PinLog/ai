#!/usr/bin/env bash
# 사용: preserve_check.sh <원본> <재구성본>
# 수치·티켓·경로·URL·문서번호 토큰의 다중집합을 비교한다.
extract() {
  grep -oE '[0-9]+(\.[0-9]+)?%?|S15P11A705-[0-9]+|#[0-9]+|[A-Za-z0-9_./-]+\.(md|py|yaml|yml|json|sh)|https?://[^ )>]+|\b[IPT][0-9]+\b' "$1" \
    | sort | uniq -c | sort -k2
}
diff <(extract "$1") <(extract "$2")
