Yes. After comparing your new plan with the earlier source audit, I would use this plan as the foundation, but I would not implement it exactly as written. It is stronger than the earlier plan in several important areas, especially lifecycle authority, controller/worker separation, durable jobs, and the explicit handling of the resume failure.

The best implementation is a hybrid of your new plan + the earlier audit, with a few changes to ordering and a few additional invariants.

1. Overall verdict

Your new plan gets the major architectural direction right:

Area	Recommendation
Worker model	Keep detached per-project workers
Controller	One authoritative EngineController
HTTP	Keep stdlib HTTP + REST + SSE
Lifecycle	Move authority to engine.db
Project telemetry	Keep per-project SQLite DB
Storage tiers	Make current permanent; treat old tiers as migration/compatibility
Event identity	Use event_id; burst number is display-only
Capture	Manifest + queue + single BurstAssembler
Git	Shadow Git remains the content/version store
AI	Controller-only, durable jobs
UI	Incremental events + true SSE
Rollback	Non-destructive
Security	Local token + Host/Origin checks
Dependencies	Only small additions such as filelock/pathspec

That direction is much better than trying to patch the current architecture indefinitely.

The most important reason is that several of the current bugs are not independent bugs. They are symptoms of multiple components believing they own lifecycle, persistence, archival, and event identity.

For example, the current /api/projects path is capable of mutating storage during a GET, while EngineWebServer creates its own Supervisor, and SessionDB permits multiple threads to share one connection without serialization.

So I would not try to fix individual symptoms indefinitely.

2. The most important change: fix the resume failure first

I agree with your N3 conclusion.

The two-Supervisor problem is more serious than it initially looks because it creates split-brain lifecycle ownership.

Your actual failure chain can look like this:

start.py
   │
   ├── creates Supervisor A
   │      └── reaper A
   │
   └── creates HTTP server
          │
          └── creates Supervisor B
                 └── reaper B

Project starts
   ↓
worker W1
   ↓
UI restart
   ↓
Supervisor A still has stale W1 metadata
   ↓
new worker W2 starts
   ↓
A notices W1 is gone
   ↓
A archives current/<slug>
   ↓
current/<slug> now belongs to W2
   ↓
live project gets archived
   ↓
"resume doesn't stick"

That is why I would make this the first acceptance test:

Restart the UI while a worker is running, then restart/resume the same project. The running project must never be archived by stale controller state.

The existing server construction really does create a second Supervisor, while its shutdown path does not terminate the Supervisor it created.

Therefore Phase 1 should explicitly eliminate:
Supervisor construction inside EngineWebServer
HTTP GET auto-archive
runtime consolidate_project_history
synthesized metadata on ordinary reads
name-vs-slug lifecycle identity
PID-only lifecycle authority

Before doing anything else.

3. One adjustment to your D2: engine.db should become authoritative, but don't overbuild it initially

Your decision:

Engine-level engine.db holds lifecycle: projects, workers, operations.

I agree.

But do not build the complete final schema in Phase 1/2.

Start with only:

engine.db
├── projects
├── workers
├── operations
└── engine_meta

Then later add:

jobs
ai_analyses
engine_events

This makes the lifecycle migration much easier to debug.

projects
project_id
slug
display_name
root_path
state
config_json
created_at
updated_at
workers
worker_id
project_id
pid
process_create_time
canonical_target
engine_root
state
started_at
last_heartbeat
exit_code
end_reason
operations
operation_id
project_id
worker_id
kind
state
requested_at
started_at
finished_at
error

This is enough to eliminate most of the current ambiguity.

The existing worker.pid, worker.status.json, project.meta, etc. then become projections, not authorities.

That distinction is extremely important.

4. I would slightly change D3

Your D3 says:

current/<slug>/ becomes permanent and last_run/history become read-only legacy sources.

I agree, but I would implement it as:

data/
    projects/
        <slug>/
            session.db
            shadow_git/
            manifest.db or manifest table
            logs/
            project.config.json

    legacy/
        current/
        last_run/
        history/

while initially keeping the old paths accessible through compatibility adapters.

You don't want the migration code to suddenly reinterpret dozens of existing filesystem paths.

The compatibility layer should make these old concepts virtual:
current
last_run
history

rather than physically moving data on every stop.

So:

GET /api/projects

can still return:

{
  "current": [...],
  "last_run": [...],
  "history": [...]
}

even though internally there is only:

project.state
session history

This preserves your frontend contract while eliminating the dangerous physical archive lifecycle.

5. N1 is actually P0, not merely a Phase 1 optimization

Your new finding about:

GET /api/project/<n>/events → consolidate_project_history()

is one of the most dangerous findings in the entire audit.

That means a harmless UI refresh can become:

GET
 ↓
open DB
 ↓
DDL / ALTER attempts
 ↓
load events
 ↓
merge history
 ↓
rewrite IDs
 ↓
wal_checkpoint(TRUNCATE)
 ↓
worker is trying to write
 ↓
possible SQLite contention
 ↓
burst sealing delays

The current events stack already mixes event retrieval with storage mutation, and the EventBundler itself is responsible for heavy post-seal work.

I would impose a hard invariant:

No GET handler may call anything whose name contains migrate, consolidate, archive, synthesize, repair, rotate, checkpoint, write, commit, move, copy, delete, or alter.

Not literally by static naming alone, but architecturally this should be the rule.

Runtime migration should only happen through:

POST /api/system/reconcile
POST /api/system/migrate
CLI system migrate
startup reconciliation

That also solves N2.

6. N2 phantom-project creation deserves its own invariant

This is a particularly good new finding.

You effectively have:

READ history
   ↓
auto_synthesize=True
   ↓
creates current/<slug>/project.meta
   ↓
GET /api/projects
   ↓
sees current/<slug>
   ↓
archives it
   ↓
history/run_2

That creates a storage feedback loop generated entirely by read operations.

So:

Mandatory rule
read_project_meta(
    auto_synthesize=False
)

for all ordinary GET paths.

Only explicit recovery/migration is allowed to synthesize missing metadata.

And synthesizing metadata should have a provenance marker:

metadata_origin = recovered

rather than silently pretending it is normal project state.

7. Your D4 is correct, but preserve a manifest even when Git exists

You propose removing:

full baseline cache
in-memory text cache
baseline/

That is correct.

But don't interpret:

Git is the content store

as:

Git replaces all metadata.

You still need the manifest.

The best model is:

Shadow Git
    = exact content/version source

Manifest
    = fast workspace state/index

SQLite events
    = semantic history

AI store
    = analysis history

The manifest should contain:

relative_path
exists
size
mtime_ns
sha256
is_binary
mode

That gives you very cheap comparison:

before = manifest[path]
after  = current_fingerprint(path)

effective_change = before != after

This is also the correct solution to your no-op concern.

8. The "effective change" rule should be formalized

Your correction here is exactly right.

Do not use:

if not diff:
    ignore

because:

empty file created
file deleted
binary changed

can all have no usable textual diff.

Use a state transition:

ABSENT
EXISTS_EMPTY
EXISTS_TEXT
EXISTS_BINARY

plus fingerprint.

Conceptually:

FileFingerprint(
    exists=True/False,
    kind=file/dir/symlink,
    size,
    sha256
)

Then:

effective = before_fingerprint != after_fingerprint

So:

Operation	Effective?
create empty file	Yes
delete empty file	Yes
binary content change	Yes
chmod-only	configurable
timestamp-only touch	No
editor save same bytes	No

This should become a core test fixture.

9. Your BurstAssembler design is one of the strongest parts of the plan

I would use it almost exactly as written.

The current implementation creates a threading.Timer for event activity, which creates the possibility of both timer churn and stale timer callbacks.

The condition-variable design eliminates:

one thread per timer
stale timer state
timer cancellation races
quiet-period hot-reload divergence
overlapping burst processors

I would add one more invariant:

There is exactly one ChangeProcessor consumer per project.

The architecture should become:

Watcher threads
     ↓
Raw FS queue
     ↓
Normalizer
     ↓
Bounded FsEvent queue
     ↓
BurstAssembler
     ↓
Ordered ChangeProcessor
     ├── ProjectWriter
     ├── GitJobQueue
     └── controller event notification

No parallel ChangeProcessors for the same project.

Different projects can still run concurrently.

10. Keep max burst = 60 seconds

I agree with your change from 20 → 60 seconds.

For this system, continuous AI-agent editing is a realistic workload.

I would use:

quiet_period = configurable
max_burst = 60s default

with:

0.5 <= quiet_period <= 3600

and:

max_burst >= quiet_period

The key is that quiet period and max burst solve two different problems.

quiet period
    = inactivity boundary

max burst
    = absolute upper bound
11. One modification to your control-channel design

I agree with:

authenticated loopback control channel
multiprocessing.connection

but I'd define the protocol before implementing it.

For example:

{
  "protocol": 1,
  "request_id": "...",
  "command": "STOP",
  "project_id": "...",
  "worker_id": "...",
  "deadline_ms": 20000
}

Responses:

{
  "protocol": 1,
  "request_id": "...",
  "ok": true,
  "state": "STOPPING"
}

Commands:

PING
STOP
SEAL
PAUSE
RESUME
RELOAD_CONFIG
SUPPRESS
RECONCILE

Every command should be idempotent.

For example:

STOP on STOPPING → success
STOP on STOPPED  → success
PAUSE on PAUSED  → success

That will greatly simplify UI retries.

The random key should be generated per worker instance and never written to command-line arguments.

12. Your process identity scheme should be even stricter

I would use all of these:

worker_id
pid
process_create_time
canonical project root
canonical worker script
engine_root

and verify:

PID exists
AND create_time approximately matches
AND executable matches
AND command line contains exact worker_id
AND target path matches

The dangerous current behavior is the substring process matching. Your proposed fix is correct.

The existing execution/process scanning is also expensive: the current fast path checks process details at roughly 0.18-second intervals and includes operations such as cwd, command-line inspection and waits.

Your adaptive scanner is the right replacement.

13. Make the reconciler the only entity allowed to declare process loss

This is an important architectural rule I would add.

Currently several things can independently decide:

"worker is dead"
"project is inactive"
"project should be archived"

That must disappear.

Instead:

Process reality
       ↓
Reconciler
       ↓
Lifecycle transition
       ↓
Storage projection
       ↓
SSE/UI

Only the reconciler may turn:

ACTIVE → INTERRUPTED

Only lifecycle code may turn:

STARTING → FAILED

No GET handler.
No random sweeper.
No UI route.
No purge_zombies().

The existing purge/recovery machinery should become an explicit administrative command rather than ordinary lifecycle behavior.

14. Storage migration: your phased approach is correct, but add a "read-only legacy" period

I would make migration:

Phase A
  discover

Phase B
  validate

Phase C
  import

Phase D
  verify

Phase E
  mark migrated

Phase F
  optional archival cleanup

Never:

discover → move → hope

For each project:

old source
↓
migration report
↓
canonical event mapping
↓
import
↓
checksum verification
↓
migration marker

Second run must produce:

already_migrated

and change nothing.

That directly supports your:

migration is a no-op on a second run.

15. I would change one thing in your rollback design

Your non-destructive rollback decision is good.

But don't make:

is_rolled_back = true

the entire semantic model.

Suppose:

E10
E11
E12
E13
E14

and rollback E12.

The workspace becomes something corresponding to E11, but E13/E14 remain valid historical events.

Therefore introduce:

event_status

or at least:

active
rolled_back
superseded

and a separate:

workspace_revision

or rollback anchor.

Conceptually:

E12 rollback
    ↓
snapshot R1
    ↓
restore
    ↓
ROLLBACK event R2

History remains:

E10
E11
E12 [rolled_back]
E13
E14
R2 ROLLBACK

The later events don't disappear.

This makes rollback auditable and preserves AI cache validity.

16. Git design: use your exact sequence, with one extra state machine

Your proposed Git sequence is right:

git add
↓
git diff --cached --quiet
↓
git commit
↓
git diff-tree

This fixes the existing --allow-empty problem. The current ShadowGit.commit_burst can still resolve HEAD after a failed commit, which makes failure look successful.

I would add:

git_status =
    pending
    running
    committed
    skipped_no_change
    skipped_overload
    failed

Then an event can safely exist even when Git fails:

event = durable
git_status = failed

A later recovery job can retry Git attribution.

This is critical:

Git failure must never mean event loss.

17. Add a durable Git-recovery mechanism

Your architecture currently has:

ProjectWriter
GitJobQueue

but there is a crash window:

event committed to SQLite
       ↓
worker dies
       ↓
Git job never runs

Therefore every event needing Git attribution must contain enough state to recover.

For example:

event_id
git_status
before_commit
after_commit
git_attempts
git_last_error

or a project-local durable git_jobs table.

Then after worker restart:

find git_status IN (pending, running)
       ↓
retry

This should be part of the Phase 5 acceptance tests.

18. AI should definitely move out of the worker

This is another major architectural improvement.

The current architecture lets worker-side code import AI/server functionality; combined with API-key handling, that creates unnecessary coupling. Your N8 diagnosis is sound.

The desired dependency graph should be:

Controller
 ├── AI provider
 ├── AI scheduler
 ├── SSE
 └── lifecycle

Worker
 ├── watcher
 ├── bundler
 ├── SQLite writer
 ├── Shadow Git
 └── control listener

Worker ──NO API KEY──> AI

The worker may produce:

event_id
patch_id
context references

and the controller turns them into AI jobs.

This also makes GPU/CPU execution profiles far cleaner.

19. Your AI architecture is basically the right final target

I would keep:

L1 Quick
L2 Detailed
L3 Architecture
L4 Risk/Impact

and:

parent_analysis_id

plus:

input_hash
model
prompt_version
sanitizer_version
profile

This is much better than the current hash-only style.

The existing AI architecture doesn't provide real progressive levels; repeated analysis tends to resolve to essentially the same cached result. Your design fixes that.

Also, the controller should own one global provider limiter:

Gemini pool
Ollama CPU pool
Ollama GPU pool
heuristic pool

rather than allowing each worker to bypass rate control.

20. Security should be brought slightly earlier

I would move these into Phase 1 rather than waiting until the full API/UI redesign:

Host allowlist
Origin checks
Content-Type enforcement
request-size limit
token authentication
remove wildcard CORS

because the existing localhost API currently exposes state-changing operations broadly.

Do the minimum hardening immediately, then do the complete API contract later.

Also make this an explicit rule:

No API key should ever exist in worker memory or worker environment unless absolutely required by a provider-specific worker mode.

Your proposed controller-only design makes that achievable.

21. SSE architecture: I would slightly change the polling mechanism

Your proposed:

controller polls max(seq) every ~400 ms

is acceptable as a transitional design.

It is still much better than the current SSE implementation, where each client periodically calls status logic and the global client queues are unbounded.

But architect it as:

DB change detector
       ↓
EngineEventBus
       ↓
bounded per-client deque
       ↓
SSE

and make the detector replaceable later.

The UI must never need to ask:

"give me all events again"

to discover that one new event appeared.

It should receive:

burst_sealed

then perform:

GET /events/<id>

once.

22. Your frontend plan is exactly the direction needed

The current UI behavior repeatedly reloads event data, which compounds the backend problem.

The final flow should be:

Initial page
   ↓
GET project summary
   ↓
GET recent event summaries
   ↓
SSE connect
   ↓
burst_sealed(event_id)
   ↓
GET event summary
   ↓
render
   ↓
user opens file
   ↓
GET diff for that file only

For 1,000-file bursts this is essential.

The UI should never transfer:

1,000 files × full patches

just because the timeline was opened.

23. One thing missing from the proposed plan: explicit configuration ownership

This should be added as a first-class component.

Right now configuration is scattered among:

CLI
project.meta
saved UI settings
defaults
worker arguments
hot reload
session metadata

The existing worker timestamp/config paths already show divergence between runtime state and persisted metadata.

Create one configuration model:

ProjectConfig
    debounce_s
    max_burst_s
    watch_policy
    scan_policy
    git_policy
    ai_profile
    ai_send_code
    provider
    auto_push

Source precedence:

CLI override
    ↓
persisted project config
    ↓
engine default

Then everything else reads the validated object.

No component should independently decide:

300
3.5
600

for the same setting.

Your identified 300s resume default plus the UI/backend default divergence makes this particularly important.

24. The final architecture I recommend

This is the structure I would target:

                         ┌──────────────────────┐
                         │      start.py        │
                         │   CLI compatibility  │
                         └──────────┬───────────┘
                                    │
                                    ▼
                         ┌──────────────────────┐
                         │    EngineController  │
                         │ lifecycle authority  │
                         └──────┬───────┬───────┘
                                │       │
                      engine.db │       │ AI/jobs
                                │       ▼
                                │  ┌───────────────┐
                                │  │ AI Scheduler  │
                                │  │ Gemini/Ollama │
                                │  └───────────────┘
                                │
                  ┌─────────────┴─────────────┐
                  │                           │
             Worker A                    Worker B
                  │                           │
          ┌───────┴───────┐           ┌───────┴───────┐
          │ Watcher       │           │ Watcher       │
          │ Normalizer    │           │ Normalizer    │
          │ Event Queue   │           │ Event Queue   │
          │ BurstAssembler│           │ BurstAssembler│
          │ ChangeProcess │           │ ChangeProcess │
          │ SQLite Writer │           │ SQLite Writer │
          │ Shadow Git    │           │ Shadow Git    │
          └───────┬───────┘           └───────┬───────┘
                  │                            │
              project DB                  project DB
                  │
                  └──────────► Controller change feed
                                      │
                                      ▼
                                  SSE clients

This keeps crash isolation without creating microservices.

25. Recommended final implementation order

I would modify your phases slightly:

Phase 0 — Evidence and freeze

Before changing behavior:

complete code bundle, including trash/, docs, frontend, .gitignore, notes
backup real data
tag current commit
capture current API/UI behavior
classify current tests
reproduce the resume failure once
establish benchmark numbers

Do not refactor yet.

Phase 1 — Stop destructive behavior

Implement only:

one Supervisor/controller per engine process
GET routes become read-only
remove runtime consolidation
disable auto-synthesis on reads
fix slug identity
UTC normalization
READ duplicate removal
DB write lock
graceful stop v1
exact worker matching
heartbeat parsing
minimum API security
default configuration object

This is the highest-value phase.

Phase 2 — Lifecycle authority

Introduce:

engine.db
EngineController
worker_id
operations
state machine
operation locks
reconciler
control channel

At the end of this phase, the old reaper/sweeper must no longer have lifecycle authority.

Phase 3 — Persistence migration

Introduce:

permanent project directories
versioned schema
event seq
burst_uid
file fingerprints
migration importer
legacy adapters

Do not delete historical directories yet.

Phase 4 — Capture pipeline

Replace:

Timer-per-event
baseline cache
path blacklist duplication

with:

IgnorePolicy
Watcher
Normalizer
bounded queue
BurstAssembler
Manifest
Reconciler
adaptive process scanner
Phase 5 — Git/change engine

Introduce:

ChangeSet
effective-change detection
single Git operation per burst
Git states
durable Git retry
large-file policy
2,000-file handling
non-destructive rollback
Phase 6 — API + SSE + frontend

Do:

API contract
token auth
incremental events
Last-Event-ID
bounded SSE
lazy diffs
hidden-tab optimization
Phase 7 — AI

Then implement:

jobs
ai_analyses
L1-L4
provider profiles
GPU/CPU/ECO
sanitization
rate limits
retry classes
controller-only providers
Phase 8 — Cleanup and production hardening

Finally:

facades
deprecated modules
backup monoliths
old suppression code
unused APIs
log rotation
metrics
fault injection
Windows CI
benchmarks
documentation
26. Important things I would NOT do

I would explicitly prohibit these during the migration:

Don't rewrite the whole project into a new framework

No:

FastAPI migration
WebSocket migration
microservices
Redis
Celery
message brokers

They don't address the current failure modes.

Don't move every file/module at once

Keep compatibility facades.

Don't delete current/last_run/history immediately

Make them compatibility views first.

Don't rewrite the frontend wholesale

Change its data flow incrementally.

Don't let tests be "fixed" just to make them green

A test should change only when the corresponding contract changes.

Don't let AI changes affect capture durability

AI must always be downstream of event persistence.

27. The test strategy should revolve around invariants, not individual bugs

The most important tests should be architectural.

Lifecycle invariants
exactly one active worker / project
no stale controller archives a live worker
dead worker never becomes cleanly stopped
Persistence invariants
GET causes zero DB writes
GET causes zero filesystem mutations
migration is idempotent
no events disappear after restart
Capture invariants
2,000 filesystem events → one burst
continuous editing → max-burst seal
hot reload affects pending burst
watcher loss → reconciliation repairs state
Git invariants
no-op → no commit
binary change → event
empty-file creation → event
2,000-file change → valid commit
Git failure → event remains durable
AI invariants
L1 ≠ L2 ≠ L3
failed AI job is retryable
secret does not reach provider
cache key includes prompt/model/sanitizer
Security invariants
foreign Origin cannot mutate
wrong Host rejected
unauthenticated mutation rejected
oversized body rejected
28. One particularly important addition: fault injection

Your proposal already mentions it, and I would make it mandatory rather than optional.

The system should intentionally simulate crashes at:

after worker PID write
after state transition
during indexing
after event SQLite commit
before Git commit
after Git commit
after patch persistence
during rollback
during migration
during shutdown

Then restart the controller.

For each crash point, the invariant should remain true:

no duplicate worker
no lost durable event
no false clean shutdown
no orphaned lock permanently
no corrupted project state

That is far more valuable than another 100 ordinary unit tests.

29. What I consider the final "locked" decisions

I would lock these now:

Decision	Status
Detached worker processes	Keep
One EngineController	Keep
engine.db lifecycle authority	Keep
Per-project SQLite	Keep
Permanent current project storage	Keep, with legacy compatibility
Git as content/version store	Keep
Manifest	Add/Keep
event_id canonical identity	Keep
non-destructive rollback	Keep, add workspace revision/status
AI controller-only	Keep
stdlib HTTP/SSE	Keep
filelock/pathspec	Keep
Timer-based bundling	Delete
runtime consolidation	Delete
GET-triggered archive	Delete
process substring matching	Delete
auto-push by default	Disable
worker API keys	Delete
filesystem tiers as lifecycle authority	Delete
30. The most important architectural principle

Everything should eventually follow this dependency direction:

                 ┌──────────────────┐
                 │    Controller    │
                 │ lifecycle / AI   │
                 └────────┬─────────┘
                          │
                  control │ events
                          │
                 ┌────────▼─────────┐
                 │      Worker      │
                 │ capture / Git    │
                 └────────┬─────────┘
                          │
                  ┌───────▼────────┐
                  │ Project DB/Git │
                  └────────────────┘

Never the reverse.

Especially:

GET → storage mutation       NO
UI → archive decision        NO
AI → capture pipeline        NO
reaper → independent state  NO
worker → lifecycle authority NO
Git failure → event loss     NO

That single principle eliminates a large portion of the current complexity.

My recommended final plan

So, between the two plans, I would choose your new plan as the primary implementation plan, but with these five modifications:

Treat N1/N2/N3 as Phase-1 blockers, not later cleanup.
Make configuration a first-class validated object, eliminating the 3.5/300/600-second divergence.
Add durable Git recovery state, so a crash between SQLite and Git cannot leave attribution permanently incomplete.
Model rollback with workspace revision/status, not only is_rolled_back.
Keep legacy storage physically available until migration verification is complete, while making current/last_run/history logical compatibility views.

With those changes, the result is a much cleaner system:

one controller
one lifecycle authority
one worker per project
one ordered capture pipeline
one project writer
one Shadow Git history
one durable event identity
one controller-owned AI scheduler
one bounded SSE stream

That is the architecture I would implement rather than continuing to patch the existing lifecycle machinery.

One more practical point: before generating the Antigravity prompts, the source bundle itself should be corrected to include scripts/trash/*, .gitignore, the complete frontend, READMEs, notes, configuration files, and all relevant tests. Otherwise the implementation agent will be operating with exactly the blind spot your N6 identified.

give all the prompt or prompts to give to antigravity for having all these changes implemented and tested well

Use these in order, one prompt at a time. Start with Prompt 0 and do not let Antigravity jump ahead. The prompts are deliberately scoped so each phase becomes a stable checkpoint before the next one.

A few rules are intentionally repeated: preserve existing CLI/API/UI paths, do not rewrite the project into FastAPI/WebSockets/microservices, do not delete historical data during migration, and never “fix” tests merely to make them pass.

Prompt Pack — 0 through Phase 1

# PROMPT 0 — Repository inventory, baseline, safety net, and complete source coverage

You are working on the existing local-first Python SPD Analysis Engine repository.

Your job in this prompt is NOT to redesign the system yet. First establish an accurate implementation baseline so subsequent refactoring is safe.

## Hard rules

1. Treat the actual repository source code as authoritative. Do not trust README claims when they differ from code.
2. Do not migrate to FastAPI, Flask, Django, WebSockets, Redis, Celery, microservices, or a different architecture.
3. Preserve existing public CLI commands, API routes, filesystem compatibility paths, and frontend behavior unless a later prompt explicitly changes an internal implementation while preserving the external contract.
4. Do not delete `current/`, `last_run/`, `history/`, `ai_data/`, existing databases, patches, shadow repositories, or user project data.
5. Never run destructive Git commands such as `git reset --hard`, `git clean -fd`, or anything that can delete user workspace data.
6. Never expose or print API keys, secrets, `.env` contents, private keys, or credential files.
7. Do not modify existing tests just to make them pass. A test may change only when its expected behavior is intentionally changed by a corresponding implementation change.
8. If platform-specific Windows behavior cannot be executed in the current environment, mark that test as NOT RUN rather than pretending it passed.

## Step 1 — Complete repository inventory

Inspect the entire repository, including:

* `start.py`
* worker modules
* supervisor/server modules
* `scripts/trash/*`
* `web/`
* dashboard/index/frontend JS
* CSS
* tests
* README files
* notes
* `.gitignore`
* `bundle_codebase.py`
* configuration files
* migration/recovery scripts
* shadow Git modules
* AI modules
* storage/rotation modules
* preflight/reaper/sweeper modules

Find any bundling/exclusion logic that accidentally excludes directories named `trash`, `patches`, `baseline`, etc. merely because the basename appears anywhere in a path.

Do not include:

* `.git/`
* virtual environments
* `__pycache__`
* generated data directories
* real credentials
* API keys
* private keys
* large runtime databases

in source bundles.

Fix `bundle_codebase.py` so the complete SOURCE CODE and TESTS are represented while secrets and runtime data remain excluded.

Check whether `ai_data/models_config.json`, `git_config.json`, `.env*`, credential files, private keys, etc. are correctly ignored. Do not print their contents.

## Step 2 — Establish current architecture

Produce a concise architecture map covering:

* CLI
* server
* Supervisor instances
* worker process
* watcher
* EventBundler
* SessionDB
* ShadowGit
* storage rotation
* process telemetry
* AI
* SSE
* frontend polling
* recovery
* trash/purge logic

Identify every place that can currently:

* start a worker
* stop a worker
* declare a worker dead
* archive a project
* mutate session/history storage
* open a writable SQLite connection
* create a Git commit
* call an AI provider
* emit SSE events

## Step 3 — Add regression tests for the already-confirmed failures

Before changing behavior, add/verify tests for:

1. only one Supervisor in UI mode
2. GET `/api/projects` is currently shown to mutate state
3. events GET currently invokes consolidation/mutation
4. heartbeat written in IST is incorrectly interpreted as `Z`
5. basename substring process matching is unsafe
6. `difflib` patch output is rejected by `git apply --check`
7. EventBundler timer race
8. shared SQLite concurrent writer corruption/race
9. duplicate READ events
10. 2,000-file Git operation
11. GET on a history-only project creating `current/<slug>/`
12. stale Supervisor archiving a replacement worker
13. duplicate event collapse from second-resolution timestamps
14. worker startup blocking before PID/status publication
15. unbounded worker.log
16. worker importing server/API/AI credential machinery
17. README/API route drift

Tests should initially fail where the bug exists. Record them as RED tests.

## Step 4 — Data safety snapshot

Create a documented, non-destructive backup procedure for:

* `current/`
* `last_run/`
* `history/`
* `ai_data/`

Do NOT move or delete the actual directories during this prompt.

## Step 5 — Baseline metrics

Record current measurements where feasible:

* full test count/pass/fail
* startup time
* worker PID publication latency
* stop latency
* status request latency
* events API payload size for a large project
* process scan duration
* idle CPU
* DB size
* WAL size
* worker.log size
* number of Supervisor/reaper threads in UI mode

## Deliverable

At the end, produce:

1. `docs/refactor/BASELINE.md`
2. a test inventory
3. a source/module ownership map
4. a list of current public CLI/API/UI contracts
5. a list of RED regression tests
6. a list of platform-specific tests that require Windows execution

Do not implement the new architecture yet.

---

# PROMPT 1 — Phase 1A.1: eliminate the two-Supervisor lifecycle split

Fix ONLY the duplicate Supervisor/lifecycle authority problem.

## Problem

UI mode currently creates a Supervisor from more than one location. The controller must have exactly one authoritative Supervisor/controller instance.

## Required implementation

1. `EngineWebServer` must no longer silently construct its own independent Supervisor.
2. The server must receive the already-authoritative Supervisor/Controller through dependency injection.
3. `start.py ui` must construct exactly one authoritative controller.
4. One-shot CLI commands may construct a temporary Supervisor/controller, but it must not start background reapers unless explicitly required.
5. The server shutdown path must shut down the authoritative controller.
6. Tests must verify that UI startup creates exactly one reaper/reconciler/lifecycle authority.
7. Multiple HTTP server objects used by tests must not silently create independent lifecycle threads.
8. Keep compatibility facades for old Supervisor imports if necessary, but they must delegate to the authoritative implementation.

## Critical invariant

There must be exactly one lifecycle authority per engine instance.

## Regression tests

Add/fix tests for:

* UI startup → exactly one Supervisor
* UI shutdown → Supervisor/reconciler stops
* second UI invocation does not create another lifecycle authority
* worker remains owned by exactly one controller
* stale test/server instances cannot archive a live project

Run targeted tests and then the full suite.

Do not touch storage migration, BurstAssembler, AI redesign, or frontend architecture in this prompt.
Stop after this scope is complete.

---

# PROMPT 2 — Phase 1A.2: make all ordinary GET/read paths truly read-only

Fix all destructive behavior triggered by reads.

## Required changes

Audit every GET route and all helper methods it calls.

For normal GET operations:

* do not archive
* do not rotate
* do not migrate
* do not consolidate
* do not synthesize metadata
* do not run DDL
* do not run ALTER TABLE
* do not checkpoint WAL
* do not write `project.meta`
* do not create project directories
* do not modify Git
* do not repair storage

Specifically:

1. Remove automatic archiving from `/api/projects` GET.
2. Remove `consolidate_project_history()` from normal events GET.
3. Events GET must use `auto_synthesize=False`.
4. Any storage repair/migration/recovery must be behind an explicit operation such as:

   * `POST /api/system/reconcile`
   * migration CLI command
   * startup reconciliation
5. `resolve_project_context()` and related read helpers must never create `current/<slug>/`.
6. A history-only project queried repeatedly must remain history-only.
7. A GET must not alter `session.db`, `session.db-wal`, filesystem mtimes, or project state.

## Tests

Write tests that snapshot:

* directory tree
* relevant file mtimes
* DB file mtime
* WAL mtime
* DB row counts
* event IDs

Then perform:

* `/api/projects`
* `/api/project/<slug>/events`
* project status GET
* session GET

and assert the snapshot is unchanged.

Also verify that no implicit checkpoint occurs.

Run the targeted tests and full suite.

Stop after this scope.

---

# PROMPT 3 — Phase 1A.3: canonical slug/project identity and path-resolution contracts

Remove all name-vs-slug ambiguity.

## Required identity model

Every project must have:

```text
project_id     stable internal ID
slug           canonical filesystem/API key
display_name   user-facing name
root_path      canonical absolute workspace path
```

The slug is the canonical key for runtime lookup.

## Fix

Audit all references to:

* `project`
* `project_name`
* `project_slug`
* display name
* path
* current/last_run/history resolution

Fix mismatches such as code expecting `project_slug` where status rows actually expose `project`.

Make all status/project records consistently contain:

```text
project_id
slug
display_name
root_path
state
```

Fix `_resolve_target_path()` and every API route/helper that depends on it.

Never use display name as a lifecycle key.

## Tests

Include:

* project name with spaces
* uppercase/lowercase slug differences
* similarly named projects
* slug prefix collisions
* old status data
* current/last_run/history compatibility data

Example:

```text
pulse
nova_pulse
pulse_dev
```

must never be confused.

Stop after this scope.

---

# PROMPT 4 — Phase 1A.4: configuration contract and debounce defaults

Create one validated project configuration model.

## Required

Introduce a single configuration object containing at least:

```text
debounce_s
max_burst_s
watch_policy
scan_policy
git_policy
ai_profile
ai_send_code
auto_push
```

Default:

```text
DEFAULT_DEBOUNCE_S = 3.5
```

Valid range:

```text
0.5 <= debounce_s <= 3600
```

Add:

```text
max_burst_s = 60.0
```

with the invariant:

```text
max_burst_s >= debounce_s
```

## Important

Eliminate independent defaults such as:

* 300 seconds during resume
* 600 seconds in frontend settings
* 3.5 seconds elsewhere

Configuration precedence must be:

```text
validated CLI override
        ↓
persisted project config
        ↓
engine default
```

Missing/invalid configuration should fall back to the validated default and expose a warning rather than silently selecting a different default.

Hot reload must update the shared configuration object.

## Tests

Test:

* missing config
* malformed config
* debounce below minimum
* debounce above maximum
* max burst smaller than debounce
* resume without saved config
* UI setting persistence
* hot reload

Stop after this scope.

---

# PROMPT 5 — Phase 1A.5: graceful stop, UTC timestamps, and shutdown ownership

Implement safe worker shutdown.

## Stop behavior

First attempt graceful control:

```text
worker control STOP
↓
wait up to 20s
↓
stop.cmd fallback
↓
terminate
↓
kill
```

Only the worker's normal execution path may run `_clean_shutdown()`.

Signal handlers must only signal an Event/flag. They must not:

* run Git
* run SQLite transactions
* flush large data
* perform network calls
* archive files

If escalation occurs, mark the session:

```text
INTERRUPTED
```

not cleanly stopped.

Do not delete lockfiles merely to make the API report success.

## Time

Create exactly one:

```text
utc_now_iso()
```

utility.

Store UTC everywhere.

Accept legacy timestamps with offsets such as `+05:30`.

Only format IST/local time at the UI boundary.

Remove duplicate `_now_ist_iso` / `_utcnow_iso` variants.

## Tests

Test:

* clean stop
* timeout escalation
* forced kill
* interrupted session
* leftover lock detection
* heartbeat age calculation
* legacy IST timestamp parsing
* signal handler behavior

A forced kill must NEVER become a clean shutdown.

Stop after this scope.

---

# PROMPT 6 — Phase 1A.6: worker identity, exact process matching, and safer spawning

Replace unsafe process matching and fragile PID ownership.

Every worker gets:

```text
worker_id = UUID
pid
process_create_time
canonical_target
engine_root
canonical_project_root
```

Pass the worker ID explicitly.

Validation requires:

1. PID exists
2. PID create time matches recorded create time within a small tolerance
3. executable matches expected executable
4. command line identifies the exact worker ID
5. canonical project root matches

Never kill a process because its command line merely contains a project substring.

Fix process sweeper logic so:

```text
pulse
```

cannot match:

```text
nova_pulse
```

and:

```text
D:\proj
```

cannot match:

```text
D:\project_two
```

## Windows spawning

Prefer `Popen` with appropriate detached/process-group/breakaway semantics.

Use WMI only as a last fallback.

If WMI is required:

* use encoded PowerShell command
* never concatenate unescaped user/project strings
* pass every required flag consistently
* log the actual launch strategy
* detect duplicate successful spawn after timeout

Test terminal-close survival if Windows is available.

Stop after this scope.

Prompt Pack — Phase 1 remainder through Phase 3

# PROMPT 7 — Phase 1B.1: SQLite concurrency and writer ownership

Fix SessionDB concurrency before building the larger pipeline.

## Immediate stopgap

Protect shared `SessionDB._execute()` with an RLock and add a transaction context manager.

Eliminate unsafe cross-thread transaction interleaving.

Readers must use read-only connections where possible.

Do NOT let one thread's rollback affect another thread's transaction.

## READ duplication

Audit:

```text
ExecutionTracker.record_execution
_on_file_read
```

A READ event must be inserted exactly once.

Choose one ownership point and remove the duplicate insert.

## Tests

Add:

* 8 threads × 5,000 writes
* concurrent read/write
* rollback isolation
* duplicate READ detection
* no lost rows
* no duplicate event identity

Then document the migration path toward the eventual single-writer architecture.

Stop after this scope.

---

# PROMPT 8 — Phase 1B.2: patch correctness and Git-apply verification

Fix diff generation.

The current `difflib` output must never generate malformed patches.

Either:

1. use Git as the patch generator, or
2. implement correctly formatted unified diffs including newline handling and `\ No newline at end of file`.

The final acceptance condition is:

```text
git apply --check
```

must succeed for every generated textual patch that is declared valid.

Do not treat an empty patch string as proof that nothing changed.

Effective-change handling will be finalized later using fingerprints.

## Tests

Cover:

* normal text modification
* new file
* deleted file
* empty file creation
* file ending with newline
* file without newline
* Unicode
* binary
* CRLF
* LF
* large files
* malformed/partial files

Stop after this scope.

---

# PROMPT 9 — Phase 1B.3: EventBundler timer race and shutdown race

Make the current EventBundler safe without yet doing the final BurstAssembler rewrite.

In the existing timer callback:

1. reacquire the bundler state lock
2. verify there is still pending work
3. verify current time satisfies the quiet period using the latest event timestamp
4. if not quiet, reschedule
5. never process a stale timer callback
6. never allow two bundle-processing callbacks to run simultaneously

Hot-reload must cancel/re-arm pending timers so the new quiet period is respected.

Ensure shutdown flushes pending work before exit when graceful shutdown is requested.

Add a maximum burst limit of 60 seconds.

## Tests

Specifically reproduce:

```text
event
timer begins
new event arrives while timer callback waits
old callback wakes
```

and prove only one correct burst is produced.

Also test:

* continuous activity
* hot reload
* stop during pending timer
* two bursts cannot process concurrently

Stop after this scope.

---

# PROMPT 10 — Phase 1B.4: process telemetry cost and READ semantics

Do not yet redesign the entire scanner. Make the current system safe enough until Phase 4.

Change process telemetry so it:

* does not scan every process every 0.18 seconds indefinitely
* uses adaptive intervals
* avoids repeated `cwd()`/`cmdline()` calls for unchanged processes
* avoids unnecessary `open_files()`
* records scan duration

READ telemetry must explicitly identify the observation source:

```text
open_handle
cmd_ref
other
```

The UI must describe these as observed/open-file telemetry, not guaranteed "reads".

Keep a TTL-based seen set instead of clearing an entire set at an arbitrary size.

## Tests

Measure:

* idle CPU
* scan duration
* scan frequency
* 1-project activity
* 100-project/process environment

Stop after this scope.

---

# PROMPT 11 — Phase 1B.5: local API security hardening

Harden the localhost server now, before the larger API rewrite.

Implement:

* Host allowlist for localhost/loopback
* reject unexpected Host values
* no wildcard CORS
* Origin validation for state-changing requests
* JSON Content-Type enforcement for JSON POST/PUT/PATCH bodies
* request body size limit
* per-launch API token
* token required for mutations
* safe error responses without secrets/paths that should not be exposed

Do not rely on CORS as authentication.

Do not put API keys in URLs.

Do not break same-origin frontend use.

## Tests

Add regression tests for:

* wrong Host
* malicious Origin
* missing token
* invalid token
* non-JSON mutation body
* oversized body
* normal same-origin request
* normal CLI/API request

Stop after this scope.

---

# PROMPT 12 — Phase 1C: package/import cleanup without changing behavior

Normalize imports so the project has one canonical package namespace.

Eliminate duplicate patterns such as:

```python
try:
    import scripts.foo
except:
    import spd_analysis_engine.scripts.foo
```

These can produce duplicate module instances and duplicate singletons.

Do a mechanical codemod where possible.

Keep compatibility facades that re-export canonical classes/functions.

Do NOT perform a large directory rewrite.

Add:

```text
pyproject.toml
```

and configure an editable install.

Pin supported Python versions consistently with the actual target.

Do not change behavior beyond import identity.

## Tests

Verify:

* imports from canonical namespace
* legacy facade imports
* no duplicate Singleton/module instances
* clean process startup
* worker startup
* test discovery

Stop after this scope.

---

# PROMPT 13 — Phase 2A: introduce EngineController + engine.db

Now implement the authoritative lifecycle layer.

Create controller modules under the existing package structure, for example:

```text
scripts/controller/
    controller.py
    registry.py
    lifecycle.py
    process.py
    recovery.py
    locks.py
    operations.py
```

Do not move the whole repository.

## engine.db

Create:

```text
projects
workers
operations
engine_meta
```

with versioned schema.

Minimum fields:

### projects

```text
project_id
slug
display_name
root_path
state
config_json
created_at
updated_at
```

### workers

```text
worker_id
project_id
pid
process_create_time
canonical_target
engine_root
state
started_at
last_heartbeat
exit_code
end_reason
```

### operations

```text
operation_id
project_id
worker_id
kind
state
requested_at
started_at
finished_at
error
```

Introduce:

```text
engine.lock
per-project operation lock
```

using an OS-level file lock plus in-process locking.

No two start/stop/resume/rollback/delete operations for the same project may execute concurrently.

## State machine

Implement:

```text
STOPPED -> STARTING
STARTING -> INDEXING
INDEXING -> ACTIVE
ACTIVE -> STOPPING
STOPPING -> STOPPED

STARTING/INDEXING/ACTIVE -> INTERRUPTED
STARTING -> FAILED

ACTIVE -> UNRESPONSIVE
any -> ORPHANED

INTERRUPTED/FAILED -> STARTING
```

Every transition must go through one lifecycle function.

Illegal transitions must fail explicitly.

## Tests

* 50 concurrent start calls => one worker
* concurrent stop/start => serialized
* idempotent start
* idempotent stop
* invalid transition rejected
* operation lock contention returns 409-equivalent behavior

Stop after this scope.

---

# PROMPT 14 — Phase 2B: controller reconciler and authenticated worker control channel

Replace the old lifecycle reaper/sweeper authority with the controller reconciler.

## Control channel

Use a loopback authenticated channel.

The worker receives a per-instance secret through environment/config, not command line.

Protocol:

```json
{
  "protocol": 1,
  "request_id": "...",
  "command": "STOP|SEAL|PAUSE|RESUME|RELOAD_CONFIG|SUPPRESS|PING",
  "project_id": "...",
  "worker_id": "...",
  "deadline_ms": 20000
}
```

Responses must include:

```text
protocol
request_id
ok
state
error
```

Commands are idempotent.

## Reconciler

Run approximately every 5 seconds.

Compare desired lifecycle state with verified process reality.

Rules:

```text
gone -> INTERRUPTED
valid unknown worker -> ADOPT
unknown unverifiable worker -> ORPHANED
stale heartbeat -> UNRESPONSIVE
```

A stale heartbeat must never automatically mean "kill".

The reconciler must NEVER archive a project solely because a previous PID died.

A worker started within its configured startup grace period must not be declared dead.

## Remove lifecycle authority from

* legacy reaper
* substring sweeper
* purge-zombies
* GET handlers
* storage rotator

These may remain temporarily as compatibility wrappers but must delegate to the controller.

## Tests

Specifically reproduce:

1. start worker
2. restart UI
3. restart/resume project
4. ensure stale controller state cannot archive the replacement worker

Also test:

* force kill
* controller restart
* worker survives UI exit
* orphan process
* stale heartbeat
* adoption

Stop after this scope.

---

# PROMPT 15 — Phase 3A: permanent project storage and schema migration framework

Convert storage to a durable project-oriented model while keeping legacy data intact.

Target model:

```text
data/
  projects/
    <slug>/
      session.db
      shadow_git/
      logs/
      project.config.json
```

Do not physically delete legacy directories.

Legacy:

```text
current/
last_run/
history/
```

become compatibility sources/views.

Implement schema migrations with:

```text
PRAGMA user_version
```

They must be:

* versioned
* transactional
* idempotent
* dry-run capable
* reportable

## Add event/session schema fields

Events:

```text
seq
burst_uid
effective_files
```

Patches:

```text
adds
dels
kind
size
hash_before
hash_after
truncated
```

Sessions:

```text
end_reason
worker_id
config_json
actor
```

Add useful indexes.

Use stable `event_id` as canonical identity.

Burst numbers are display-only and must never resolve an event.

## Migration behavior

For a legacy project:

1. discover
2. validate
3. import
4. verify
5. write migration marker

Never move data until verification succeeds.

Second migration run must be a no-op.

## Tests

* migration dry run
* real migration
* second migration
* corrupted source handling
* legacy event ID mapping
* canonical ID preservation
* migration interruption/restart

Stop after this scope.

Prompt Pack — Phase 4 through final verification

# PROMPT 16 — Phase 3B: single ProjectWriter and durable event persistence

Implement the final single-writer architecture for each project.

Worker-owned data:

```text
events
patches
sessions
manifest
Git attribution state
capture metadata
```

Controller-owned data:

```text
engine.db
jobs
ai_analyses
controller flags
```

Create a `ProjectWriter` thread that owns the only write connection for project telemetry.

Other threads submit operations.

Use:

```text
Future
```

for durability-critical writes.

Best-effort telemetry may use non-blocking submission and increment a drop counter.

Batch writes approximately every 50 ms or N operations while preserving event ordering.

Readers use read-only SQLite connections.

## Tests

* 8 × 5,000 concurrent logical writes
* zero lost durable rows
* no transaction cross-contamination
* GET does not touch WAL
* worker restart drains/reconciles pending writer operations
* write queue backpressure is observable

Stop after this scope.

---

# PROMPT 17 — Phase 4A: IgnorePolicy, manifest, and watcher normalization

Create one shared `IgnorePolicy`.

Layers:

1. built-in ignored directories/files
2. project `.gitignore`
3. optional `.spdignore`
4. sensitive-file blocklist

Use relative paths from project root.

Sensitive defaults include patterns such as:

```text
.env*
*.pem
*.key
id_rsa*
credentials*
*.pfx
```

Sensitive files must be metadata-only by default.

Expose methods:

```text
should_watch
should_index
should_diff
should_stage
should_send_to_ai
```

Do not use a naïve "any path part contains venv/patches/baseline" rule.

Use cheap string-prefix/path checks before expensive path resolution in hot paths.

## Manifest

Create:

```text
file_manifest
```

with:

```text
relative_path
exists
size
mtime_ns
sha256
is_binary
mode
```

Hash only when size/mtime indicates potential change.

## Watcher

Normalize raw watcher events into:

```text
FsEvent(
    kind,
    rel_path,
    timestamp
)
```

Coalesce editor atomic-save sequences.

Directory moves must be represented as file-level renames using manifest knowledge where possible.

## Tests

* `.gitignore`
* `.spdignore`
* sensitive files
* nested directories named "patches"/"baseline"
* rename
* editor temp-file saves
* empty files
* binary files

Stop after this scope.

---

# PROMPT 18 — Phase 4B: bounded event queue and final BurstAssembler

Replace Timer-per-event bundling with a condition-variable BurstAssembler.

No `threading.Timer` objects for event debouncing.

Algorithm:

```python
while running:
    wait until pending or stopped

    due = min(
        last_event + quiet_period,
        first_event + max_burst
    )

    wait until due or notification

    recompute due after every notification

    extract exactly one ordered bundle
    enqueue downstream outside the lock
```

`add()` and `set_policy()` must notify the condition variable.

There must be exactly one ordered ChangeProcessor consumer per project.

The processor must never overlap burst N and burst N+1.

## Queue

Use a bounded queue, approximately 20,000 events by default.

On overflow:

```text
needs_reconcile = true
drop counter++
```

Do not grow memory without bound.

## Reconciliation

Periodic manifest scan roughly every 60 seconds while idle.

Immediate reconciliation after:

* queue overflow
* watcher restart
* system sleep/wake gap
* detected filesystem event loss

## Effective-change calculation

Use fingerprints:

```text
before != after
```

not diff-string emptiness.

This must correctly detect:

* empty file creation
* deletion
* binary changes
* textual changes

but ignore timestamp-only rewrites when file content is unchanged.

## Tests

* 2,000 events -> expected bundle behavior
* continuous editing -> max-burst seal
* quiet-period seal
* hot config reload
* timer race impossible
* queue overflow -> reconciliation
* watcher restart -> recovery
* process shutdown with pending bundle

Stop after this scope.

---

# PROMPT 19 — Phase 4C: adaptive process telemetry

Redesign process telemetry to avoid hot polling.

Default behavior:

* idle around 2s
* never below 0.5s
* next interval adapts based on scan duration
* newly seen processes receive deeper inspection
* repeated unchanged processes avoid repeated expensive calls

Use `open_files()` only for allowlisted IDE/application processes and at a slower interval.

Use TTL-based seen handles.

Keep capture durability independent of telemetry failures.

Process telemetry must never be capable of blocking the event writer for a long period.

## Tests

Measure and report:

* idle CPU
* scan duration
* scan interval
* RSS
* queue size
* event processing latency

Stop after this scope.

---

# PROMPT 20 — Phase 5A: ChangeSet, Git content store, 1,000+/2,000-file scalability

Make `ChangeSet` the central domain object produced after burst sealing.

It should contain:

```text
burst_uid
event_id/sequence
files
effective_files
renames
before fingerprints
after fingerprints
```

Git workflow per effective burst:

```text
git add
↓
git diff --cached --quiet
↓
git commit
↓
git diff-tree
```

Do NOT use `--allow-empty`.

For large path sets, avoid command-line-length limits.

Use pathspec-from-file/NUL mode where necessary.

Prefer one Git operation per burst instead of one subprocess per file.

Use one recursive diff-tree operation to obtain the patch.

Every Git call gets an explicit timeout.

Set:

```text
GIT_TERMINAL_PROMPT=0
```

Configure Shadow Git appropriately:

```text
gc.auto=0
core.longpaths=true
core.quotepath=false
core.autocrlf=false
core.fsmonitor=false
```

Git state:

```text
pending
running
committed
skipped_no_change
skipped_overload
failed
```

Git failure must never lose the already-persisted event.

## Durable recovery

Persist enough information to retry unfinished Git work after a worker crash.

## Size policy

Default:

```text
max_text_bytes ≈ 1 MB
max_patch_bytes ≈ 2 MB
```

Above limits:

* record hashes/sizes/metadata
* mark truncated
* do not load huge file contents into RAM

## Tests

Mandatory:

* 1,000 files
* 2,000 files
* long paths
* binary changes
* empty-file changes
* no-op burst
* Git failure
* Git timeout
* worker crash during Git commit
* recovery after restart

Stop after this scope.

---

# PROMPT 21 — Phase 5B: rollback redesign and safe auto-push

## Rollback

Rollback must be non-destructive.

Sequence:

```text
PAUSE
↓
seal pending burst
↓
pre-rollback snapshot commit
↓
compute dry-run file plan
↓
user confirmation
↓
restore required files
↓
mark affected historical events as rolled_back/superseded
↓
write ROLLBACK event
↓
resume
```

Never use a bare:

```text
git clean -fd
```

as rollback implementation.

Keep all event IDs stable.

Model rollback state separately from event identity.

A rollback must be undoable.

## Auto-push

Disable unsafe automatic primary-repository pushing.

If implemented:

* opt-in per project
* branch allowlist
* refuse default branch unless explicitly allowed
* only stage milestone files
* refuse during merge/rebase
* refuse when index.lock exists
* credentials via GIT_ASKPASS
* never put tokens in remote URLs
* dry-run support
* scrub stderr
* distinguish "nothing to commit" from all other failures

Push runs as a controller background job, never in the capture worker's critical path.

## Tests

* rollback then undo rollback
* byte-identical restoration
* rollback during pending burst
* Git failure
* primary branch protection
* push skipped because index locked
* failed commit must never accidentally push

Stop after this scope.

---

# PROMPT 22 — Phase 6A: API contract, incremental events, and SSE redesign

Create one API contract table/source that defines:

```text
method
path
handler
authentication
request schema
response schema
mutation/read classification
```

Register all aliases from one contract definition.

Preserve existing public routes.

Do not break `/api/projects/...` versus legacy `/api/project/...` compatibility where currently supported.

## Events API

Primary endpoint:

```text
GET /events?since_id=&limit=&types=&session_id=
```

returns summaries only.

Single event:

```text
GET /events/<id>
```

returns event + file list.

Diff:

```text
GET /events/<id>/files/<path>/diff
```

or the existing equivalent compatibility path.

Do not return full diff bodies by default.

Legacy event endpoint may remain with a bounded result.

## SSE

Use bounded per-client queues.

Support:

```text
Last-Event-ID
```

Events should include at least:

```text
burst_sealed
status_changed
job_progress
ai_completed
ai_failed
```

Do not send full project payloads on every heartbeat.

Coalesce status/progress updates.

Cap SSE clients.

The SSE channel must be a real event stream, not repeated full GET polling.

## Tests

* reconnect with Last-Event-ID
* missed events replay
* bounded client queue
* multiple clients
* slow client
* client disconnect
* unauthorized SSE
* large project

Stop after this scope.

---

# PROMPT 23 — Phase 6B: frontend incremental data flow

Refactor the frontend incrementally; do NOT rewrite it wholesale.

Initial page load:

```text
project summary
↓
recent event summaries
↓
SSE
```

On `burst_sealed`:

```text
fetch only that event
```

On file expansion:

```text
fetch only the selected diff
```

`handleLivePing` must never re-fetch the entire event list.

Pause nonessential polling when:

```text
document.hidden === true
```

Use windowed file lists for very large bursts.

Split JS by responsibility as files are touched:

```text
api/
state/
views/
components/
```

but do not perform an unrelated mass frontend restructure.

## Tests

* 5,000-event project
* 1,000-file burst
* tab hidden/visible
* SSE reconnect
* multiple browser tabs
* memory remains bounded
* diff fetched only when opened

Stop after this scope.

---

# PROMPT 24 — Phase 7A: controller-owned AI jobs and persistent analysis cache

Move AI execution entirely into the controller.

Workers may enqueue analysis jobs but must never hold provider API keys or make provider calls.

Create durable:

```text
jobs
ai_analyses
```

## AI analysis identity

Unique cache key must include:

```text
target_kind
target_id
profile
input_hash
model
provider
prompt_version
sanitizer_version
context_hash
```

Statuses:

```text
queued
running
done
failed
superseded
```

Store:

```text
result_json
parent_analysis_id
token counts
latency
error class
created_at
completed_at
```

Never store an AI failure as a successful result.

## Progressive levels

Implement:

```text
L1 Quick
L2 Detailed
L3 Architecture
L4 Risk/Impact
```

Higher levels may receive lower-level output plus the diff/context with an explicit:

```text
expand, don't restate
```

instruction.

L1/L2/L3/L4 are separate stored analyses.

## Jobs

Use bounded provider pools.

Retry rules:

* 429/503 → retry with backoff
* auth errors → no automatic retry
* JSON parse error → one repair retry
* timeout → one smaller-context retry
* permanent failure → failed state + Retry action

Use job leases/heartbeats so a killed controller job can resume.

Stop after this scope.

---

# PROMPT 25 — Phase 7B: AI provider profiles, sanitization, and GPU/CPU modes

Implement provider execution profiles:

```text
NORMAL
ECO
GPU_AI
OFFLINE
```

Profiles control:

* concurrency
* timeout
* context size
* Ollama `num_ctx`
* Ollama `num_gpu`
* Ollama `keep_alive`
* polling intensity

Do not let execution profile changes alter capture durability.

## Gemini

* API key via header
* never URL query parameter
* no silent model substitution
* validate configured model
* shared rate limiter

## Ollama

Probe runtime status where available.

Expose whether the configured model is actually loaded on GPU.

Use profile-driven context/concurrency.

## Sanitizer

Before provider calls, redact:

* AWS credentials
* GitHub tokens
* Google AI keys
* OpenAI-style keys
* Slack tokens
* JWTs
* PEM private keys
* `password=`
* `secret=`
* `token=`
* `api_key=`
* credential-bearing URLs

Sensitive files should be represented as:

```text
[REDACTED FILE: <path>]
```

Record sanitizer version in the cache key.

Cloud analysis must require a per-project consent/enable setting.

Update README privacy statements so they accurately state that cloud AI may receive sanitized project content when enabled.

## Tests

* secret fixture never reaches mocked provider
* model mismatch
* rate limit
* timeout
* GPU profile
* CPU profile
* cache invalidation after prompt version change
* cache invalidation after sanitizer version change

Stop after this scope.

---

# PROMPT 26 — Phase 8: structure cleanup, observability, logs, and documentation

Now clean up the architecture only where the preceding phases made old code unnecessary.

Remove/deprecate obsolete pieces such as:

* old in-process suppression machinery
* unused zombie purge paths
* obsolete synthesis mixins
* backup monoliths
* dead duplicate code
* lifecycle reapers that are no longer authoritative
* old baseline cache implementation
* obsolete timer bundler

Do not delete compatibility facades until all callers have migrated.

## Logging

Rotate worker logs, approximately:

```text
5 MB × 3
```

Use structured JSON Lines.

Every log event should have as applicable:

```text
timestamp
project_id
worker_id
operation_id
event_id
component
level
message
```

## Health endpoint

Expose:

```text
queue depths
drop counters
writer lag
Git queue status
AI queue status
scan durations
active workers
reconciler health
```

## Documentation

Correct README/documentation drift:

* actual API endpoints
* actual CLI flags
* actual Python support
* actual storage behavior
* actual privacy behavior
* actual AI/cloud behavior
* actual SSE endpoint
* actual shutdown semantics

Do not claim "no data leaves the machine" when cloud AI is enabled.

Stop after this scope.

---

# PROMPT 27 — Phase 8B: fault injection, benchmarks, and final regression suite

Create a deterministic fault-injection framework behind:

```text
SPD_TEST_FAULTS
```

Support injected crashes at:

```text
after pid write
during STARTING
during INDEXING
inside DB transaction
after event durability
before Git commit
during Git commit
after Git commit
during migration
during rollback
during shutdown
during pending burst
```

Run a real worker against a temporary project.

Restart controller/worker and verify invariants.

## Required invariant suite

### Lifecycle

* exactly one active worker/project
* no stale controller can archive a live project
* killed worker is not cleanly stopped
* start/stop operations are idempotent

### Storage

* GETs cause no mutation
* migration is idempotent
* no lost events
* no duplicate event IDs
* WAL remains stable under reads

### Capture

* 2,000 events behave correctly
* max burst enforced
* queue overflow recovered
* watcher restart repaired

### Git

* no-op creates no commit
* empty file creation creates effective change
* deletion creates effective change
* binary change preserved
* 2,000-file burst succeeds
* Git failure does not lose event
* unfinished Git job recovers

### Rollback

* rollback is auditable
* event IDs remain stable
* undo rollback restores bytes

### AI

* levels are distinct
* failures are retryable
* secrets are redacted
* cache identity is complete
* killed jobs resume

### API/UI

* unauthorized mutation rejected
* SSE bounded
* Last-Event-ID replay works
* large event payloads remain bounded
* hidden browser tabs do not hammer backend

## Benchmark

Create `bench/` measurements for:

```text
events/sec
burst seal p50/p95
Git time
2,000-file burst time
RSS
idle CPU
DB growth
SSE memory/client
events payload size
AI queue latency
```

Do not invent performance thresholds. Measure a baseline first, then define justified thresholds.

Run:

1. focused tests
2. unit suite
3. integration suite
4. end-to-end suite
5. fault injection
6. benchmark smoke tests

If Windows-only tests cannot be executed, explicitly report them as unrun.

Stop after this scope.

---

# PROMPT 28 — FINAL ARCHITECTURE AUDIT AND DRIFT CHECK

Do NOT add new features.

Review the complete repository against the following invariants:

```text
1. Exactly one lifecycle authority per engine.
2. Exactly one active worker per project.
3. GET endpoints are read-only.
4. Filesystem history directories are never lifecycle authorities.
5. event_id is canonical identity.
6. Burst numbers are display-only.
7. UTC is the persisted time standard.
8. Worker identity requires exact verification.
9. Process death is handled by reconciler, not GET/reaper/sweeper.
10. One ordered capture consumer per project.
11. Event queue is bounded.
12. Project DB has one writer.
13. Git failure never loses an event.
14. No-op bursts do not create Git commits.
15. Empty-file, delete, and binary changes remain observable.
16. Rollback is non-destructive and auditable.
17. Worker processes do not own AI API keys.
18. AI jobs are durable and retryable.
19. AI cache identity includes model/prompt/sanitizer/context.
20. Sensitive data is sanitized before cloud-provider calls.
21. SSE queues are bounded.
22. SSE supports replay with Last-Event-ID.
23. Frontend does not repeatedly reload complete event history.
24. Legacy paths/routes remain compatible unless explicitly deprecated.
25. Historical data is not deleted during migration.
26. Migrations are idempotent.
27. No destructive "cleanup" happens because a GET occurred.
28. No substring process matching remains.
29. No wildcard CORS remains for mutation endpoints.
30. No hidden second Supervisor exists.
```

Search the actual source code for violations of every invariant.

Also search for these dangerous patterns:

```text
threading.Timer
--allow-empty
git add <huge list>
psutil.Process(...).terminate()
process_iter(... substring ...)
consolidate_project_history(
auto_synthesize=True
wal_checkpoint
rglob("*")
*.lock cleanup
Access-Control-Allow-Origin: *
api key in URL
```

Classify each remaining occurrence as:

```text
SAFE
COMPATIBILITY
NEEDS_REPLACEMENT
TEST-ONLY
```

Do not blindly remove code that still has an intentional compatibility purpose.

## Final deliverable

Create:

```text
docs/refactor/FINAL_AUDIT.md
```

containing:

* implemented phases
* remaining compatibility code
* known limitations
* tests passed
* tests not run and why
* Windows-only verification status
* benchmark results
* migration status
* architectural invariant status
* any remaining technical debt

The project is considered complete only when all critical invariants are either GREEN or explicitly documented as an intentional compatibility exception.

Do not claim success merely because the test suite is green. The architecture and invariants must also be verified.

Send them sequentially. For Phase 1, use Prompts 1–12 individually rather than giving Antigravity a giant Phase-1 rewrite; that is the most important area because the current lifecycle, storage, DB, and configuration bugs interact.

After Prompt 28, the next useful step is a separate production-readiness prompt focused purely on Windows 10/11 behavior, 2,000+ file benchmarks, crash recovery, and long-running soak tests.
