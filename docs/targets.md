# Production targets

These are the default synchronous-client limits and acceptance targets. They
are operating targets, not a promise of Graph service capacity.

| Area | Target |
| --- | --- |
| Single file | 10 GiB maximum by default; larger files require an explicit policy |
| One tree | 100 GiB, 100,000 items, depth 64, and 1,000 pages by default |
| Concurrency | Bounded by `OperationPolicy.max_concurrency` per manager; default is one |
| Tenants | One configured site/drive boundary per manager; create separate managers for separate tenants |
| Request recovery | Retry budget of 5 safe-method attempts; `Retry-After` capped at 60 seconds; operation deadline 1 hour |
| Local recovery | Atomic downloads retain the previous destination; ambiguous uploads expose session state for reconciliation |
| Latency target | Measure non-transfer Graph operations at p95 ≤ 1.5 seconds in staging before increasing concurrency |
| Rate-limit target | Honor Graph throttling and record every retry/throttle event; do not add client-side parallelism without evidence |

Run the deterministic CPU baseline with:

```bash
python verification/benchmark_targets.py
```

This measures QuickXorHash and metadata conversion only. It does not claim a
Graph/network SLO. A staging run must supply the latency, transfer, throttling,
failure-recovery, and tenant-capacity evidence before changing the sequential
default or adding async APIs, queues, caches, or broader frameworks.
