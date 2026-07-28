#include "deps/include/v8-context.h"
#include "deps/include/v8-initialization.h"
#include "deps/include/v8-locker.h"
#include "deps/include/v8-platform.h"

#include "context.h"
#include "isolate.h"
#include "libplatform/libplatform.h"

using namespace v8;

auto default_platform = platform::NewDefaultPlatform();
ArrayBuffer::Allocator* default_allocator;

extern "C" {

/********** Isolate **********/

#define ISOLATE_SCOPE(iso)           \
  Locker locker(iso);                \
  Isolate::Scope isolate_scope(iso); \
  HandleScope handle_scope(iso);

void Init() {
#ifdef _WIN32
  V8::InitializeExternalStartupData(".");
#endif
  V8::InitializePlatform(default_platform.get());
  V8::Initialize();

  default_allocator = ArrayBuffer::Allocator::NewDefaultAllocator();
  return;
}

// Called when the heap is about to exceed its limit. Terminating here is what
// turns "the process dies" into "this script dies", but the return value is the
// subtle part, so it is worth writing down what the extra room is actually for.
//
// It is NOT for unwinding. TerminateExecution only sets a flag; V8 unwinds at
// the next interrupt check and needs very little to do it. The room is for the
// allocation that triggered this callback, which V8 RETRIES once a new limit is
// returned. So the headroom has to be at least as large as whatever single
// allocation was in flight.
//
// That is why this is proportional rather than a fixed increment. Returning
// `current + K` means any single allocation larger than K aborts the process —
// the exact outcome this callback exists to avoid. Measured on a 16 MiB isolate,
// terminating a script that allocates:
//
//   allocation shape        2x     +256K   +1M    +4M    +16M
//   many small objects      ok     ok      ok     ok     ok
//   1 MiB strings           ok     ABORT   ok     ok     ok
//   one 400 MiB string      ABORT  ABORT   ABORT  ABORT  ABORT
//
// Doubling therefore means "tolerate a single allocation up to the current
// limit". It is not a safety margin that always works — the last row shows a
// large enough allocation defeats it too — but it scales with the isolate, so a
// bigger heap tolerates a bigger allocation without anyone tuning a constant.
//
// The doubling costs address space, not resident memory: execution stops at this
// instant, so V8 never fills the space it was just granted. Measured in an
// embedder running four workers that all hit their ceiling, RSS came to roughly
// `1.1 x limit + 15 MiB` per isolate, not `2 x limit`.
//
// AutomaticallyRestoreInitialHeapLimit (installed alongside this callback) puts
// the configured limit back once the heap drains, so the raise is temporary.
size_t NearMemoryLimitCallback(void* data, size_t current_heap_limit, size_t initial_heap_limit)
{
  auto iso = static_cast<Isolate*>(data);
  iso->TerminateExecution();

  // if we return the initial heap limit, the VM will crash, so here we give it room to exit gracefully
  return current_heap_limit * 2;
}

IsolatePtr NewIsolate(IsolateConstraintsPtr constraints, int terminate_on_heap_limit) {
  Isolate::CreateParams params;
  params.array_buffer_allocator = default_allocator;

  if (constraints != nullptr) {
    ResourceConstraints rc;
    rc.ConfigureDefaultsFromHeapSize(
      constraints->initial_heap_size_in_bytes,
      constraints->maximum_heap_size_in_bytes
    );
    params.constraints = rc;
  }

  Isolate* iso = Isolate::New(params);
  Locker locker(iso);
  Isolate::Scope isolate_scope(iso);
  HandleScope handle_scope(iso);

  iso->SetCaptureStackTraceForUncaughtExceptions(true);

  if (terminate_on_heap_limit) {
    // Catch the OOM condition and stop execution instead of killing the process.
    iso->AddNearHeapLimitCallback(NearMemoryLimitCallback, iso);

    // The callback above must raise the limit -- returning the initial one makes
    // V8 crash while unwinding -- so the isolate is left over its configured
    // ceiling once it has fired. Ask V8 to put the configured value back once
    // the heap has dropped below half of it, which it can do after the
    // terminated script's garbage is collected. Without this the isolate keeps
    // the doubled ceiling for the rest of its life, and every further trip
    // doubles it again.
    iso->AutomaticallyRestoreInitialHeapLimit(0.5);
  }

  // Create a Context for internal use
  m_ctx* ctx = new m_ctx;
  ctx->ptr.Reset(iso, Context::New(iso));
  ctx->iso = iso;
  iso->SetData(0, ctx);

  return iso;
}

void IsolatePerformMicrotaskCheckpoint(IsolatePtr iso) {
  ISOLATE_SCOPE(iso)
  iso->PerformMicrotaskCheckpoint();
}

void IsolateDispose(IsolatePtr iso) {
  if (iso == nullptr) {
    return;
  }
  auto ctx = static_cast<m_ctx*>(iso->GetData(0));
  ContextFree(ctx);

  iso->Dispose();
}

void IsolateTerminateExecution(IsolatePtr iso) {
  iso->TerminateExecution();
}

void IsolateCancelTerminateExecution(IsolatePtr iso) {
  iso->CancelTerminateExecution();
}

int IsolateIsExecutionTerminating(IsolatePtr iso) {
  return iso->IsExecutionTerminating();
}

IsolateHStatistics IsolationGetHeapStatistics(IsolatePtr iso) {
  if (iso == nullptr) {
    return IsolateHStatistics{0};
  }
  v8::HeapStatistics hs;
  iso->GetHeapStatistics(&hs);

  return IsolateHStatistics{hs.total_heap_size(),
                            hs.total_heap_size_executable(),
                            hs.total_physical_size(),
                            hs.total_available_size(),
                            hs.used_heap_size(),
                            hs.heap_size_limit(),
                            hs.malloced_memory(),
                            hs.external_memory(),
                            hs.peak_malloced_memory(),
                            hs.number_of_native_contexts(),
                            hs.number_of_detached_contexts()};
}
}
