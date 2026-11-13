# Authoritative Controller Subsystem (`scripts/controller/`)

## Module Purpose
The `controller` module provides the authoritative lifecycle control plane for the SPD Analysis Engine. It maintains the global state database (`data/engine.db`), serializes lifecycle operations via OS file locks, enforces formal state machine transitions, operates the background Reconciler daemon, manages loopback IPC control channels, and tracks durable background jobs.

---

## File Roster & Technical Reference

- **`__init__.py`**
  - Package exports for `LifecycleController`, `EngineRegistry`, and lifecycle models.

- **`control_channel.py`**
  - **Classes:** `ControlChannelHandler`, `WorkerControlServer`
  - **Core Functions:** `send_worker_command(address, authkey, command, **kwargs)`
  - **Purpose:** Manages authenticated loopback IPC channels between the controller and workers using `multiprocessing.connection`. Dispatches idempotent commands (`PING`, `STOP`, `SEAL`, `PAUSE`, `RESUME`, `RELOAD_CONFIG`).

- **`controller.py`**
  - **Classes:** `LifecycleController`
  - **Purpose:** Central coordinator that manages project registration, authoritative state transitions, worker spawning delegates, and reconciler execution.

- **`jobs.py`**
  - **Classes:** `JobRecord`, `AiAnalysisRecord`, `ControllerFlag`
  - **Core Functions:** `init_controller_tables(conn)`, `create_job(...)`, `update_job_state(...)`, `get_job(...)`, `record_ai_analysis(...)`, `get_ai_analysis(...)`, `set_controller_flag(...)`, `get_controller_flag(...)`, `enqueue_auto_push_job(...)`
  - **Purpose:** Persistent job queue and analysis registry inside `engine.db`. Coordinates background AI synthesis and auto-push tasks with lease-tracking recovery.

- **`lifecycle.py`**
  - **Classes:** `ProjectState`, `InvalidStateTransitionError`
  - **Core Functions:** `is_valid_transition(from_state, to_state)`, `transition_state(conn, project_id, new_state)`
  - **Purpose:** Formal state machine governing legal transitions:
    - Normal: `STOPPED` $\rightarrow$ `STARTING` $\rightarrow$ `INDEXING` $\rightarrow$ `ACTIVE` $\rightarrow$ `STOPPING` $\rightarrow$ `STOPPED`.
    - Anomalous: `INTERRUPTED`, `FAILED`, `UNRESPONSIVE`, `ORPHANED`.

- **`locks.py`**
  - **Classes:** `OperationLockConflictError`, `_InProcessLockRegistry`, `FileLock`, `EngineLock`, `ProjectOperationLock`
  - **Purpose:** Multi-layered locking engine combining cross-process OS file locks with in-process reentrant threading locks, ensuring serialized start/stop operations per project.

- **`operations.py`**
  - **Classes:** `OperationExecutionError`
  - **Core Functions:** `track_operation(registry, project_id, kind, worker_id)`
  - **Purpose:** Context manager that writes an immutable audit record to `engine.db.operations` for every lifecycle mutation.

- **`process.py`**
  - **Core Functions:** `is_process_alive(pid)`, `get_process_create_time(pid)`, `stop_process_cooperatively(pid, deadline_s)`
  - **Purpose:** Low-level OS process inspection and cooperative shutdown execution.

- **`reconciler.py`**
  - **Classes:** `Reconciler`, `ReconcilerDaemon`
  - **Purpose:** Authoritative daemon that runs every ~5 seconds to verify OS process reality against `engine.db`. Sole entity permitted to declare worker process loss (`ACTIVE` $\rightarrow$ `INTERRUPTED`).

- **`recovery.py`**
  - **Core Functions:** `reconcile_orphans(registry)`
  - **Purpose:** Startup recovery utility that cleans orphaned worker records and repairs ungraceful host shutdown artifacts.

- **`registry.py`**
  - **Classes:** `ProjectRecord`, `WorkerRecord`, `OperationRecord`, `EngineRegistry`
  - **Purpose:** Data access layer providing atomic CRUD operations on `engine.db` tables (`projects`, `workers`, `operations`, `engine_meta`).
