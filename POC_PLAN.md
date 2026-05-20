# BrightEdge URL Crawler — PoC Plan

This document covers how the Part 1 PoC moves to the production system described in DESIGN.md. It includes the phases, time estimates, blockers, release plan, and success criteria for each step.

---

## 1. Phases and Milestones

The work is split into four phases. Each one has a single milestone that signals it is done.

### Phase 1 — Single-URL PoC (complete)

This is what was built in Part 1. The live service at `https://brightedge-crawler-773180407845.us-central1.run.app` takes a single URL, fetches it, parses the metadata, runs YAKE for topic extraction, and returns a structured JSON response. It has been tested on the three assignment URLs (Amazon, CNN, REI) and several edge cases (bad URLs, timeouts, anti-bot challenge pages, PDF URLs).

**Milestone:** Live API on Cloud Run responding to POST /extract with valid JSON for the assignment's test URLs.

### Phase 2 — Queue and worker pool (1 million URLs)

The first real scaling step. The single Cloud Run service becomes a pool of workers reading from a queue.

What gets added:
- A Pub/Sub queue partitioned by domain
- An ingestion service that reads URLs from a text file and pushes them to the queue
- The Cloud Run service modified to pull URLs from the queue instead of serving HTTP requests
- Autoscaling based on queue depth
- Basic monitoring (queue depth, worker count, fetch rate)

**Milestone:** Process 1 million URLs end-to-end in a test run with no manual intervention. Write results to a hot store (BigTable). Success rate above 95%.

### Phase 3 — Storage tiers and headless fallback (100 million URLs)

The full architecture comes online. All three storage tiers are wired up, the headless browser path is added, and SLO/SLA monitoring is set up.

What gets added:
- Cold store writes to GCS Parquet
- Search index writes to OpenSearch
- Playwright worker pool for JavaScript-rendered pages, with HTTP-first / headless-fallback routing
- Retries with backoff and a per-domain skip list for anti-bot challenges
- MySQL input format support (alongside the text-file path)
- SLO/SLA dashboards and alert rules

**Milestone:** Process 100 million URLs over one week, hitting the SLOs from DESIGN.md §3.8 (98% success rate, p95 latency under 5 minutes, queue depth under 500,000).

### Phase 4 — Full production scale (1 billion URLs per month)

The target scale. Mostly tuning and hardening at this point, not new features.

What gets done:
- Autoscaling tuned against real overnight burst patterns
- Per-domain rate limits tuned based on actual block rates
- Cost dashboard reviewed weekly to catch drift (headless usage creeping up, cold-store reads, etc.)
- Runbook written for the failure modes in DESIGN.md §5.1
- Cross-region failover documented (still manual for v1)

**Milestone:** One billion URLs per month sustained for four consecutive weeks, holding all SLAs, monthly cost staying inside the $12,000 to $18,000 target.

---

## 2. Time Estimates

Each phase below assumes a team of three engineers — one each on the data plane (queue, workers, fetching), the storage plane (three tiers and the unified schema), and the operations plane (monitoring, alerts, runbooks). Estimates assume the team is dedicated to this project. Part-time involvement would extend each phase by roughly 30 to 50 percent.

| Phase | What gets built | Estimated time |
|---|---|---|
| Phase 1 — Single-URL PoC | Live API on Cloud Run, tested on assignment URLs | **Complete (2 days)** |
| Phase 2 — Queue and worker pool | Pub/Sub, ingestion, autoscaling, basic monitoring | **3 to 4 weeks** |
| Phase 3 — Storage tiers and headless fallback | All three storage tiers, Playwright pool, SLO dashboards | **6 to 8 weeks** |
| Phase 4 — Full production scale | Tuning, runbooks, cost optimization, sustained 1B/month | **6 to 8 weeks** |
| **Total to production scale** | | **15 to 20 weeks (about 4 to 5 months)** |

Phase 3 is the longest building phase because three storage tiers, the headless browser path, and the full monitoring stack all come online at the same time. Each is independently non-trivial, and they have to be tested together before scale-up validation can start.

Phase 4 looks short relative to its scope, but most of the work is wall-clock time rather than engineering time. The system runs at increasing scale (100M → 250M → 500M → 1B URLs), with about a week of observation at each step to surface bottlenecks. The final milestone — four consecutive weeks of sustained 1B-per-month processing — cannot be compressed, since it is what proves the system holds up.

A few risks to these estimates:

- **Anti-bot complexity.** If a major target domain has unexpectedly strict bot protection, Phase 3 can extend by one to two weeks while we set up rotating egress IPs or work around the restriction.
- **Scale-up surprises.** Going from 100M to 1B in Phase 4 often reveals bottlenecks that did not appear at the smaller scale — usually around queue throughput, per-domain rate limits, or storage write rates. The two-week buffer in Phase 4 accounts for this.
- **Headless throughput.** Playwright performance at scale is hard to predict without measuring it on real domains. If the actual headless rate is higher than the 5% assumed in the design, Phase 3 timeline grows.

---

## 3. Known and Unknown Blockers

Some blockers we already understand from the PoC and the design. Others will only show up at scale. The first set is planned for; the second set is where buffer time goes.

### Known blockers

- **Anti-bot challenges.** Around 2 to 5 percent of URLs return a challenge page. The PoC handles this with detection and a 502 response; at scale we add retries and a headless fallback. Rate is unpredictable per domain.
- **JavaScript-rendered pages.** Around 5 percent need Playwright (the REI case from PoC testing). The blocker is cost — headless costs 100× more per URL, so routing has to stay conservative.
- **Per-domain rate limits.** The right rate is different for every domain. Setting it conservatively wastes capacity; setting it too high gets the IP blocked. Tuning needs real traffic, not guessing.
- **Deduplication at scale.** Around 30 to 40 percent of input URLs are duplicates. A naive in-memory set does not scale to billions. The likely answer is a Bloom filter plus a key-value lookup, but the parameters need to be benchmarked.

### Unknown blockers

- **Parser failures on specific domains.** The PoC tested three URLs. Production sees millions of unique domains, and some will break BeautifulSoup in ways we can only find at scale.
- **Storage tier bottlenecks.** Writing to three tiers in parallel works at 1M URLs. At 1B URLs per month, one of them — most likely OpenSearch — may become the bottleneck. We will not know which until we run that volume.
- **Autoscaling behavior under bursts.** Cloud Run scaling from 20 to 100 instances during overnight ingestion bursts looks smooth on paper. In practice, cold-start latency and scale-up step size always reveal something on the first real burst.
- **Anti-bot escalation over time.** Cloudflare updates its rules regularly. A 2 percent challenge rate today might climb to 10 percent in six months if a major target domain tightens protection. The skip list handles the immediate case; the long-term pattern is unpredictable.

---

## 4. Trivial vs Hard Problems

Not all of the work in this plan carries the same risk. Some is configuration, some is real engineering. Separating the two shows where timeline slips actually come from.

### Trivial

These are well-documented, standard pieces. The work is wiring things up, not solving anything new.

- Pub/Sub setup and queue partitioning
- Cloud Run autoscaling configuration
- BigTable and GCS Parquet writes (standard client libraries)
- OpenSearch index setup
- SLO/SLA dashboards in Cloud Monitoring
- Adding MySQL input format alongside the text-file path

### Hard

These are where iteration and learning from real traffic matter. They are the parts of the plan most likely to take longer than estimated.

- **Deduplication at billion-URL scale.** The Bloom filter + key-value lookup approach works in theory; the parameters (false-positive rate, memory footprint, partitioning) need real benchmarking.
- **Adaptive per-domain rate limiting.** The control loop that backs off when responses slow down or error rates rise is harder than a fixed-rate limiter, and takes iteration to stabilize.
- **HTTP-first / headless-fallback routing accuracy.** Misclassifying a small percentage of URLs as needing headless can blow up the cost budget. The detection signals (empty body, JS framework patterns) need tuning over time.
- **Cloud Run autoscaling under bursts.** Scale-up step size, cold-start latency, and concurrency settings all interact. Real bursts always expose at least one surprise the first time.

The hard problems are where Phase 3 and Phase 4 time is actually spent. The trivial problems are mostly Phase 2.

---

## 5. Release Plan

Each phase ships through the same release path: staging, then canary, then gradual rollout, then full production. The goal is to catch problems while the blast radius is small, not after they affect the whole system.

### Staging

Every change first runs in a staging environment that mirrors production at smaller scale (one worker instance, a single-partition queue, dummy storage backends). Staging catches obvious problems — code that doesn't compile, broken integrations, schema mismatches — before any real traffic is involved.

Staging is also where load testing happens before a phase milestone. For example, before Phase 2 ships, we run a 10,000-URL test in staging to verify the queue, workers, and storage write path all work together end-to-end.

### Canary

Once staging is clean, the change rolls out to a small slice of real production traffic — usually 1 percent. The canary runs alongside the existing system, processing the same kind of input but at a much smaller volume. Metrics (success rate, latency, cost per URL) are compared against the baseline.

A canary stays in place for at least 24 hours per phase, longer for Phase 3 and Phase 4 changes. If anything degrades — error rates rise, latency spikes, costs drift — the canary is rolled back automatically and the change goes back to staging.

### Gradual rollout

If the canary holds for the observation window, traffic ramps up in steps: 1% → 10% → 50% → 100%. Each step waits long enough to see at least one full ingestion cycle (overnight batch arrival, autoscale burst, daily settle).

For Phase 4 specifically, the gradual rollout maps to the scale milestones: 100M, then 250M, then 500M, then 1B URLs per month. Each step is held for a week of observation before moving up.

### Rollback

Every phase has a defined rollback path. For Phases 2 and 3, this means routing traffic back to the previous worker pool version (Cloud Run revision pinning makes this a one-command operation). For Phase 4, rollback means reducing the traffic share rather than reverting code, since the changes are tuning rather than new features.

Rollback criteria are explicit: success rate dropping more than 2 percentage points below baseline, p95 latency increasing by more than 50 percent, cost per URL increasing by more than 20 percent.

---

## 6. Success Criteria

Each phase has concrete numbers that have to hold before it is considered complete. These are the criteria used to evaluate whether the design is working — not just whether the code runs, but whether it meets the targets set in DESIGN.md §3.8.

### Phase 2 success criteria

- Process 1 million URLs end-to-end with no manual intervention
- Success rate above 95% (the remaining 5% accounts for anti-bot, timeouts, and unreachable domains)
- Queue depth stays under 100,000 throughout the test run
- Average per-URL processing cost stays within 2× of the design estimate ($0.000015 per URL)

### Phase 3 success criteria

- Process 100 million URLs over a one-week run
- Hit all SLOs from DESIGN.md §3.8 (98% success rate, p95 latency under 5 minutes, queue depth under 500,000, worker availability above 99.5%, storage write latency p95 under 200ms)
- Headless usage stays at or below 6 percent of total fetches (slightly above the 5% design assumption to allow for variance)
- All three storage tiers (BigTable, GCS Parquet, OpenSearch) hold their write SLOs without backpressure

### Phase 4 success criteria

- Sustain 1 billion URLs per month for four consecutive weeks
- All SLAs from DESIGN.md §3.8 met (95% success rate, p95 under 15 minutes, etc.)
- Monthly cost stays inside the $12,000 to $18,000 target
- No more than three high-severity incidents per month (defined as anything that requires manual intervention beyond the runbook)
- Cost dashboard catches at least one cost drift before it shows up in the monthly bill (validates that the monitoring works)

### Overall PoC success

The PoC is considered successful when Phase 4 holds for four consecutive weeks. At that point, the system has demonstrated that the design from DESIGN.md works at the target scale, within the target cost, and with the target reliability. Anything past that — multi-region failover, real-time freshness, full-text search — is out of scope for the PoC and belongs to a v2 roadmap.