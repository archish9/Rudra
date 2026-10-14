# Batching measurement

Prices Vaayu's N>1 batched decode: N Rudra sessions run concurrently vs the
same N run serially. This is the decision gate for the parallel-dispatch work.

## Configure Vaayu

    ATTN_IMPLEMENTATION="sdpa"
    MAX_BATCH_SIZE=3
    PREFIX_SESSIONS=3        # >= 3
    KV_CACHE_MODE="auto"

Point Rudra's model roles at that Vaayu server (`rudra config list`).

## Run

    python scripts/measure_batching.py --n 3 --task "<task>" --vaayu-stats vaayu-stats.json

`vaayu-stats.json` holds Vaayu's scheduler counters, e.g.
`{"batches_run": 5, "gate1_refused": 0, "gate2_refused": 0}`. Each session runs
`rudra --auto --allow-shell` in its own temp project; wall time comes from each
run's `usage.json` (`run.wall_seconds`). Output goes to
`scripts/out/measure-<timestamp>.json` and stdout.

## Reading the result

- `serial_wall_sum`: total wall time of the N serial runs.
- `concurrent_wall_max`: wall time of the slowest concurrent run.
- `batches_run`: batches Vaayu actually formed. 0 means nothing was batched.
- `batching_helped`: true only if `batches_run > 0` AND
  `concurrent_wall_max < serial_wall_sum`. False means concurrency bought
  nothing (or no batch formed), so do not build on it.
- `gate1_refused` / `gate2_refused`: admission refusals; high values explain a
  false result.
