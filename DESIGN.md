## 1. Overview

The PoC built in Part 1 handles one URL per request. This design extends the same code to handle one billion URLs per month by running multiple worker instances behind a queue, with storage separated into tiers based on how the data is accessed.

There are three main ideas behind this design:

**Stateless workers.** Workers do not keep track of which URLs they are processing. The queue is the source of truth. If a worker crashes mid-fetch, the URL goes back on the queue and another worker picks it up. This lets us add, remove, or restart workers without coordination.

**Tiered fetching.** Around 95% of URLs can be handled by the simple HTTP fetcher from the PoC. The remaining 5% are JavaScript-rendered pages (like the REI blog seen in testing) that need a headless browser, which costs around 100 times more per URL. Deciding which URLs go through which path is a major cost decision.

**Separate storage tiers.** Metadata, raw HTML, and search content have different access patterns. We use a key-value store for fast metadata lookups, object storage for raw HTML kept for longer-term analysis, and a search index for content discovery. Using a single database for all three would not scale efficiently.

At steady state, the system runs around 15 to 20 workers, scaling up to 100 during high-volume periods. The estimated cost is between $12,000 and $18,000 per month for one billion URLs, which works out to about $0.000015 per URL.

---

## 2. Scale Requirements

### 2.1 Input and output

The input is a list of URLs delivered as text files in object storage or as rows in a MySQL table. URLs are partitioned by domain and year-month, for example `amazon.com/2025-07/` or `walmart.com/2025-07/`. The output is structured metadata (title, description, body content, Open Graph tags, language) and a ranked list of topics for each URL, stored in a queryable form.

The shape of the work is the same as the PoC. The difference is volume.

### 2.2 The numbers behind the design

The design is anchored on processing one billion URLs per month. From this number, every other capacity decision follows:

| Metric | Value |
|---|---|
| Monthly volume | 1,000,000,000 URLs |
| Daily average | ~33M URLs |
| Per-second average | ~385 URLs/sec |
| Per-second peak (3× burst) | ~1,150 URLs/sec |
| Average fetch time (from PoC) | ~600 ms |
| Worker concurrency | 50 in-flight fetches (async I/O) |
| Effective per-worker throughput | ~80 URLs/sec |

Dividing the average rate by per-worker throughput gives about five workers as the minimum needed. To handle peaks and slow domains, the steady-state pool is sized at twenty workers and autoscales up to one hundred during ingestion bursts.

Per-worker memory is around 300 MB, which fits comfortably in a small container. This number is small because the PoC chose YAKE for topic extraction instead of a transformer-based model. A transformer model would push the per-worker footprint past one gigabyte and increase compute costs significantly.

The three-times peak is based on the assumption that most input batches arrive overnight. A single domain partition can land on the queue all at once, and without autoscaling the queue depth would grow quickly and the latency SLO would be missed.

### 2.3 Problems that only appear at scale

The PoC handles a single fetch correctly. Three problems become important once we are running thousands of fetches per second:

**Politeness.** Sending many requests to the same domain from one IP address gets the IP blocked. We need per-domain rate limiting, queue partitioning so workers do not all hit the same site at once, and rotating egress IPs for sites with strict bot protection.

**Deduplication.** The same URL often appears in the input in different forms — with tracking parameters, mobile prefixes, or as a short link that redirects to the same destination. Without deduplication, around 30 to 40 percent of compute is wasted on URLs that have already been processed. We dedupe twice: at ingestion using a canonical URL form, and at fetch resolution using the final URL after redirects.

**Anti-bot challenges.** Around 2 to 5 percent of URLs return a challenge page from services like Cloudflare or Akamai instead of the actual content. The PoC already detects this case by matching known challenge phrases against the page title and combining that with the upstream HTTP status code (the "Just a moment..." case from testing). At scale, we add retries with exponential backoff, a headless browser fallback for sites where this happens often, and a skip list for domains that consistently challenge us.

These three problems shape most of the architecture in Section 3.

### 2.4 Out of scope

To keep this design focused, the following are not covered:

- Full-text search over crawled content. This would be handled by Elasticsearch or OpenSearch and is a separate system.
- Real-time crawling of newly published URLs. We assume batch ingestion on a daily or hourly schedule.
- Legal and robots.txt compliance checks. We assume input URLs are pre-approved per BrightEdge's existing policy.
- Multi-region deployment. The PoC and this design are single-region (us-central1). Multi-region failover is a future extension.

---

## 3. Architecture

To operationalize collection at the billion-URL scale, the system needs more than the PoC code, it needs ingestion, queueing, worker management, and storage built around it. This section describes how those pieces fit together and how data flows through them.

### 3.1 How a URL moves through the system

A URL passes through five stages between input and storage: ingestion, queueing, fetching, parsing and topic extraction, and storage. The fetching and parsing stages run the same code as the Part 1 PoC. The other three stages are what scale adds.

In order:

1. **Ingestion** reads the input (text file or MySQL table), canonicalizes each URL, drops duplicates within the batch, and pushes the cleaned URLs onto the queue.
2. **The queue** holds all URLs that still need to be processed. It is partitioned by domain so workers do not all hit the same site at once.
3. **A worker** pulls a batch of URLs from the queue, decides whether each URL needs a regular HTTP fetch or a headless browser fetch, and runs the fetch.
4. **The same worker** then parses the response with BeautifulSoup, runs YAKE for topic extraction, and produces the structured output.
5. **Storage writes** go to three places in parallel: a key-value store for fast lookups, an object store for raw HTML and analytical queries, and a search index for content discovery.

If anything fails along the way, the URL goes back on the queue with a retry counter. After three failed attempts, it is sent to a separate dead-letter queue for inspection.

### 3.2 Ingestion

The ingestion service is the only component that handles the input format. It reads URLs from text files in object storage (one URL per line, partitioned by domain and year-month) or from a MySQL table. For each URL, it produces a canonical form by lowercasing the host, stripping tracking parameters like `utm_*` and `ref=`, and normalizing mobile prefixes (so `m.cnn.com` and `www.cnn.com` collapse to the same key).

Duplicates within the same batch are dropped before they reach the queue. This is the first dedupe pass and removes the most obvious overlap. A second pass happens later, after redirects are followed.

The output of ingestion is a clean stream of canonical URLs. Everything downstream sees the same shape regardless of whether the input came from a file or a database.

### 3.3 Queue

The queue is the source of truth for what work is left. We use Google Pub/Sub here because the rest of the system runs on Cloud Run and the two integrate natively. On AWS, the equivalent choice would be SQS.

The queue is partitioned by domain. Each domain gets its own logical sub-queue, and workers consume from sub-queues in a round-robin pattern. This is what enforces politeness — even if `amazon.com` has a million URLs in the queue, workers will not all rush to fetch from it simultaneously. Each sub-queue also has a per-domain rate limit (configurable, defaulting to 10 requests per second per domain), which prevents bursts even when many workers are active.

Messages in the queue are kept until they are acknowledged by a worker. If a worker crashes mid-fetch, the message is automatically returned to the queue after a visibility timeout (60 seconds, matching the worker's request timeout) and another worker picks it up.

### 3.4 Worker pool

A worker is a containerized version of the Part 1 code, running on Cloud Run. Each worker pulls a batch of 50 URLs from the queue, runs all 50 fetches concurrently using async I/O (the same `httpx` pattern as the PoC), parses each response, runs YAKE, and writes the results to storage.

The worker pool is sized at 20 instances at steady state. Cloud Run autoscales based on queue depth: when the queue grows above a threshold (10,000 pending messages), more instances are added, up to a hard cap of 100. When the queue drains, instances are removed automatically.

Workers are stateless. They hold no information about which URLs they are working on beyond the in-flight batch. This means an instance can be killed at any time without losing work, and we can deploy new versions of the worker code with a rolling update.

### 3.5 Tiered fetching

About 95% of URLs work with the simple HTTP fetcher from the PoC. The other 5% are JavaScript-rendered pages where the HTML returned by a basic fetch is essentially empty (just a `<div id="root">` waiting for JS to populate it). The REI blog tested in Part 1 is an example.

These pages need a headless browser to run the JavaScript before the HTML can be parsed. We maintain a separate pool of Playwright workers for this. A headless fetch costs roughly 100 times more per URL than an HTTP fetch (more memory, more CPU, longer fetch time of 5 to 10 seconds versus under a second), so we route conservatively.

URLs end up on the headless path in two ways. Some domains are known to be JS-heavy (REI, certain SPA-based news sites) and go straight to headless. For everything else, the HTTP fetcher runs first, and if the result has metadata but an empty body (the same case the PoC handles), the URL is re-queued for a headless retry.

### 3.6 Storage

Three stores, each with a different access pattern:

**Hot store: BigTable (or DynamoDB on AWS).** This is the primary store for processed metadata. The key is the canonical URL, and the value is the structured output (title, description, topics, language, timestamps). Reads are single-digit milliseconds, which is what the downstream BrightEdge platform needs when querying "what do we know about this URL." Storage is cheap (a few cents per GB per month) because the records are small (around 2 KB each on average).

**Cold store: Cloud Storage with Parquet files (or S3 on AWS).** Raw HTML and the full body content go here, partitioned by domain and year-month to match the input layout. Parquet is used because analytical queries (BigQuery or Athena) are columnar and benefit from compression. We expect very few reads from this store, but the data needs to be kept for re-processing if the parser or topic extractor is updated. Storage costs around a third of a cent per GB per month, and at billion-URL scale this is the cheapest tier per record despite holding the most data.

**Search index: OpenSearch.** A subset of the structured output (title, description, topics, language, and an excerpt of body content) is indexed here for content discovery and analytical queries from the application layer. This is the most expensive store per record because of the index overhead, but only a fraction of URLs need to be searchable — for example, only the latest crawl per canonical URL, not historical versions.

Writes to all three stores happen in parallel after parsing finishes. A write failure on one store does not block the others; the worker logs the partial write and the URL is re-queued for retry, where the writes are idempotent.

### 3.7 Unified data schema

The same logical record is stored in all three tiers; what differs is the physical layout.

The logical record:

```json
{
  "url": "https://www.example.com/page",
  "canonical_url": "https://example.com/page",
  "final_url": "https://example.com/page",
  "domain": "example.com",
  "crawl_id": "uuid-v4",
  "fetched_at": "2025-07-15T14:23:11Z",
  "http_status_code": 200,
  "status": "success",
  "metadata": {
    "title": "...",
    "description": "...",
    "og_type": "article",
    "og_image": "...",
    "language": "en",
    "author": "...",
    "published_date": "..."
  },
  "topics": [
    {"keyword": "...", "score": 0.97},
    {"keyword": "...", "score": 0.95}
  ],
  "body_content": "...",
  "fetch_time_ms": 612.4,
  "fetcher_used": "http",
  "schema_version": "1.0"
}
```

The hot store keeps everything except `body_content` (which is too large for fast key-value reads). The cold store keeps the full record including `body_content` and the original raw HTML. The search index keeps `url`, `domain`, `metadata`, `topics`, and a 500-character excerpt of `body_content`.

The `schema_version` field is important. Over time, fields will be added (new metadata types, scoring changes), and `schema_version` lets the downstream platform know which fields to expect. Old records keep their original version; new records get the latest.

### 3.8 SLOs and SLAs

The system is internal — it serves the BrightEdge platform, not external customers — so SLAs are defined against the platform's needs rather than against an outside contract. The SLOs (the internal targets) are stricter than the SLAs to leave room for recovery before a contract breach.

| | SLO (internal target) | SLA (commitment to platform) |
|---|---|---|
| Per-URL processing success rate | 98% | 95% |
| End-to-end latency (URL submitted to result available) | p95 under 5 minutes | p95 under 15 minutes |
| Queue depth | under 500,000 at all times | under 2 million sustained |
| Worker availability | 99.5% | 99% |
| Storage write latency | p95 under 200ms | p95 under 1 second |

The 98% success-rate target accounts for the 2-5% of URLs that hit anti-bot challenges or are otherwise un-crawlable. These are surfaced with clear error codes (the `502 upstream_error` and anti-bot challenge detection from Part 1) rather than counted as silent failures.

The 5-minute end-to-end target is dominated by queue wait time during bursts, not by fetch time. Most URLs are fetched and stored in under two seconds; the rest of the time is spent waiting in the queue for a worker to pick them up.

### 3.9 Monitoring and observability

Three kinds of signals are tracked: throughput, health, and cost.

**Throughput metrics** are emitted from every worker on every fetch:

- `urls_processed_total` (counter, by status code and fetcher type)
- `fetch_duration_ms` (histogram)
- `parse_duration_ms` (histogram)
- `queue_depth` (gauge, by domain partition)
- `worker_count` (gauge)

These feed a real-time dashboard (Cloud Monitoring or Grafana) showing throughput per second, p50/p95/p99 latencies, and queue depth per domain. The dashboard is what an on-call engineer looks at first during an incident.

**Health metrics** track the state of the system itself:

- Worker error rate (5xx responses per worker per minute)
- Queue lag (oldest unprocessed message age)
- Storage write error rate
- Anti-bot challenge rate per domain

Alerts fire when error rates exceed thresholds (worker error rate above 5% for 5 minutes, queue lag above 30 minutes), when anti-bot challenge rate spikes for a specific domain (likely an IP got flagged), or when the worker pool fails to autoscale during a queue surge.

**Cost metrics** are tracked weekly rather than in real time:

- Compute hours per million URLs processed
- Egress bandwidth per million URLs
- Storage cost per tier per month
- Headless fetch usage as a percentage of total fetches

The cost dashboard is what catches creeping inefficiencies — for example, if the headless-routing logic starts misclassifying URLs and headless usage drifts from 5% to 15%, the monthly bill jumps before anyone notices.

Logs are structured (JSON) and shipped to Cloud Logging. Each log line includes the `crawl_id`, the URL, the fetcher used, and timing breakdowns. This makes it possible to trace a single problematic URL through the system end-to-end without grepping through unstructured text.

---

## 4. Cost Analysis

### 4.1 Where the money goes

At one billion URLs per month, the steady-state cost lands between $12,000 and $18,000. The breakdown below is based on Google Cloud pricing as of 2025; AWS pricing is comparable within ~10 percent.

| Cost driver | Monthly cost (estimate) | Share of total |
|---|---|---|
| Worker compute (Cloud Run, ~20 instances steady) | $7,500 | ~50% |
| Headless browser pool (5% of URLs, ~50M/month) | $1,500 | ~10% |
| Egress bandwidth (1B URLs × ~50KB avg response) | $3,000 | ~20% |
| Hot store (BigTable, ~2TB) | $800 | ~5% |
| Cold store (GCS Parquet, ~50TB) | $200 | ~1% |
| Search index (OpenSearch, ~10% of URLs) | $1,500 | ~10% |
| Queue (Pub/Sub) | $400 | ~3% |
| Monitoring and logging | $200 | ~1% |
| **Total** | **~$15,000** | 100% |

Per-URL cost works out to roughly $0.000015, or 1.5 thousandths of a cent.

### 4.2 Where the savings come from

Three decisions in the design account for most of the cost optimization, and each one was made deliberately rather than inherited from a default choice.

**Choosing YAKE over a transformer-based topic extractor.** The PoC chose YAKE because it has no model weights to load and runs in CPU-only containers. A transformer alternative like KeyBERT would require either GPU instances (5-10× more expensive per worker hour) or larger CPU instances with more memory (3-4× more expensive). At billion-URL scale, this single choice avoids roughly $20,000 to $40,000 per month in additional compute.

**Routing JS-rendered pages conservatively.** Headless browser fetches cost around 100 times more per URL than HTTP fetches (more CPU, more memory, longer fetch times). The design routes only known JS-heavy domains directly to headless, and uses an "HTTP-first, fall back to headless" approach for everything else. If headless usage drifted from 5% to 20% of total traffic, the monthly bill would jump by around $4,500.

**Aggressive deduplication.** Around 30-40% of input URLs are duplicates after canonicalization (tracking parameters, mobile prefixes, redirects). Without deduplication, the system would process 1.3-1.4 billion URLs to deliver 1 billion unique results, adding roughly $5,000 per month for no additional output.

### 4.3 Where the cost can grow unexpectedly

Three things cause the monthly bill to grow faster than the URL count:

**Anti-bot challenge rates rising.** If a major domain starts blocking the IPs more aggressively, the challenge rate can jump from 2% to 10% overnight. Each blocked URL still consumes worker time before the challenge is detected, and retries multiply this. The cost dashboard tracks challenge rate per domain so this is visible early.

**Headless misclassification.** If the HTTP-first router incorrectly flags too many pages as needing headless retry, headless usage drifts up and the bill follows. The dashboard tracks headless usage as a percentage of total fetches; a sustained increase past 10% triggers a review.

**Cold-store reads.** Cold storage is cheap to write and cheap to keep, but expensive to read at scale (egress costs apply). If downstream systems start querying cold storage frequently rather than going through the search index, the egress bill can grow quickly.

---

## 5. Reliability and Failure Modes

### 5.1 What can go wrong

The PoC handles a few failure modes correctly: bad URLs, timeouts, HTTP errors, anti-bot challenge pages. At scale, more failure modes appear because the system has more moving parts. The table below lists the main ones and how each is handled.

| Failure | What it looks like | How the system handles it |
|---|---|---|
| Worker crash mid-fetch | Pod dies, in-flight URL not acknowledged | Queue visibility timeout returns the URL after 60s; another worker picks it up |
| Single URL timeout | Fetch exceeds 30s | Worker logs the failure, URL goes back to queue with retry counter |
| Repeated URL failure | Same URL fails 3 times in a row | Sent to dead-letter queue for manual inspection |
| Storage write failure (one tier) | Hot store write succeeds but cold store fails | Worker logs partial write; URL re-queued; writes are idempotent so duplicates are safe |
| Storage write failure (all tiers) | All three writes fail | URL re-queued; if failure persists, alert fires |
| Queue backlog growing | Ingestion rate exceeds processing rate | Autoscaler adds workers up to cap; if cap is hit, alert fires and operator decides whether to slow ingestion |
| Anti-bot challenge for a domain | Domain starts returning challenge pages for all URLs | Per-domain challenge rate alert fires; URLs from that domain get throttled and eventually moved to headless path |
| Cloud Run regional outage | Workers in us-central1 unreachable | Manual failover to us-east1; documented in runbook, not automated for v1 |

### 5.2 What we deliberately don't try to handle

A few failure modes are out of scope by design:

- **Permanent loss of a URL.** If a URL hits the dead-letter queue, it stays there until manually inspected. We do not retry indefinitely.
- **Partial corruption of input files.** If an input batch contains malformed URLs, those URLs are skipped at ingestion with a log entry, but the rest of the batch continues. We do not roll back the entire batch.
- **Cross-region failover.** This is a v2 concern. The v1 system runs single-region; an outage means a delay, not data loss, since the queue and storage are regional-redundant within us-central1.

### 5.3 Next steps

Once the system is stable at one billion URLs per month, the next optimizations would be:

- **Adaptive politeness.** Rather than a fixed per-domain rate limit, the system could learn domain-specific rate limits by tracking when responses start slowing down or returning errors.
- **Content-type-aware extraction.** PDFs, images, and video pages currently fall back to metadata-only. A specialized parser for PDF (the assignment's PDF URL case) would extend coverage without adding much compute.
- **Incremental crawling.** For URLs that have been crawled before, a HEAD request with `If-Modified-Since` can skip the full fetch if the page has not changed. This is most useful for domains that are crawled repeatedly.
- **Multi-region deployment.** Once steady-state traffic justifies it, deploying to us-east1 alongside us-central1 would reduce latency for users in the eastern US and provide automatic failover.