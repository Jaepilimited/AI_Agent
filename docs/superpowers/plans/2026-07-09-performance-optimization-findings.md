# 성능 최적화 감사 결과 (2026-07-09)

## Baseline (측정: 2026-07-09)

- pm2 restart_time (skin1004-prod): 0
- pm2 memory (skin1004-prod): 58.4mb
- pm2 cpu (skin1004-prod): 0%
- pm2 uptime (skin1004-prod): 21h
- exec mode: fork_mode

`pm2 status` 원본 출력 (skin1004-prod 행):
```
│ 11 │ skin1004-prod   │ default │ N/A │ fork │ 53604 │ 21h │ 0 │ online │ 0% │ 58.4mb │ DB_PC │ disabled │
```

참고: 프로세스는 유휴 상태(요청 없음)로 측정되어 CPU 0%. 실제 요청 처리 중
리소스 사용량은 이번 정적 감사로는 직접 측정하지 못하며, 아래 감사 결과의
"경량 계측" 권고 항목을 적용한 뒤 운영 트래픽에서 재측정이 필요하다.
