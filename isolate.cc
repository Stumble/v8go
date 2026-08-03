#include "deps/include/v8-context.h"
#include "deps/include/v8-initialization.h"
#include "deps/include/v8-locker.h"
#include "deps/include/v8-platform.h"
#include "deps/include/v8-primitive.h"

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

uint32_t IsolateGetContinuationPreservedEmbedderData(IsolatePtr iso) {
  ISOLATE_SCOPE(iso)
  Local<Value> data = iso->GetContinuationPreservedEmbedderData();
  if (data.IsEmpty() || !data->IsUint32()) {
    return 0;
  }
  return data.As<Uint32>()->Value();
}

void IsolateSetContinuationPreservedEmbedderData(IsolatePtr iso,
                                                 uint32_t token) {
  ISOLATE_SCOPE(iso)
  if (token == 0) {
    iso->SetContinuationPreservedEmbedderData(Undefined(iso));
    return;
  }
  iso->SetContinuationPreservedEmbedderData(
      Integer::NewFromUnsigned(iso, token));
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
