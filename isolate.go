// Copyright 2019 Roger Chapman and the v8go contributors. All rights reserved.
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

package v8go

// #include <stdlib.h>
// #include "isolate.h"
import "C"

import (
	"sync"
	"unsafe"
)

// Isolate is a JavaScript VM instance with its own heap and
// garbage collector. Most applications will create one isolate
// with many V8 contexts for execution.
type Isolate struct {
	ptr C.IsolatePtr

	cbMutex sync.RWMutex
	cbSeq   int
	cbs     map[int]FunctionCallbackWithError

	null      *Value
	undefined *Value
}

// HeapStatistics represents V8 isolate heap statistics
type HeapStatistics struct {
	TotalHeapSize            uint64
	TotalHeapSizeExecutable  uint64
	TotalPhysicalSize        uint64
	TotalAvailableSize       uint64
	UsedHeapSize             uint64
	HeapSizeLimit            uint64
	MallocedMemory           uint64
	ExternalMemory           uint64
	PeakMallocedMemory       uint64
	NumberOfNativeContexts   uint64
	NumberOfDetachedContexts uint64
}

type resourceConstraints struct {
	InitialHeapSizeInBytes uint64
	MaxHeapSizeInBytes     uint64
}

// IsolateOption configures an Isolate on creation.
type IsolateOption func(*isolateConfig)

// isolateConfig holds the configuration for creating an isolate.
type isolateConfig struct {
	resourceConstraints  *resourceConstraints
	terminateOnHeapLimit bool
}

// WithResourceConstraints sets memory constraints for the isolate.
//
// On its own this only bounds the heap. What happens when that bound is reached
// is V8's default: FatalProcessOutOfMemory, which ends the PROCESS. Pair it with
// WithTerminateOnHeapLimit to stop just the running script instead.
func WithResourceConstraints(initialHeapSizeInBytes, maxHeapSizeInBytes uint64) IsolateOption {
	return func(config *isolateConfig) {
		config.resourceConstraints = &resourceConstraints{
			InitialHeapSizeInBytes: initialHeapSizeInBytes,
			MaxHeapSizeInBytes:     maxHeapSizeInBytes,
		}
	}
}

// WithTerminateOnHeapLimit makes reaching the heap limit terminate the running
// script rather than the process.
//
// V8's default is FatalProcessOutOfMemory: one isolate exhausting its heap takes
// the whole process down, along with every other isolate in it. For an embedder
// running untrusted code in a per-task isolate that is the wrong trade, and this
// option inverts it -- the offending script is terminated, the isolate is left
// unusable, and everything else keeps running.
//
// It is opt-in because the other trade is legitimate too: an embedder whose MAIN
// isolate exceeds the memory it was configured for often WANTS the process to
// exit and be restarted by its supervisor, rather than to continue in a degraded
// state. Leave this off for that isolate and on for disposable ones.
//
// Terminating requires temporarily raising the heap limit -- V8 allocates while
// unwinding, and refusing it there crashes the VM -- so an isolate that has hit
// the limit is briefly over its ceiling. V8 restores the configured value once
// the heap drops back below half of it.
func WithTerminateOnHeapLimit() IsolateOption {
	return func(config *isolateConfig) {
		config.terminateOnHeapLimit = true
	}
}

// NewIsolate creates a new V8 isolate with the provided options.
// Only one thread may access a given isolate at a time, but different
// threads may access different isolates simultaneously.
// When an isolate is no longer used its resources should be freed
// by calling iso.Dispose().
// An *Isolate can be used as a v8go.ContextOption to create a new
// Context, rather than creating a new default Isolate.
func NewIsolate(opts ...IsolateOption) *Isolate {
	initializeIfNecessary()

	config := &isolateConfig{}
	for _, opt := range opts {
		opt(config)
	}

	var cConstraints C.IsolateConstraintsPtr
	if config.resourceConstraints != nil {
		cConstraints = &C.IsolateConstraints{
			initial_heap_size_in_bytes: C.size_t(config.resourceConstraints.InitialHeapSizeInBytes),
			maximum_heap_size_in_bytes: C.size_t(config.resourceConstraints.MaxHeapSizeInBytes),
		}
	}

	var cTerminateOnHeapLimit C.int
	if config.terminateOnHeapLimit {
		cTerminateOnHeapLimit = 1
	}

	iso := &Isolate{
		ptr: C.NewIsolate(cConstraints, cTerminateOnHeapLimit),
		cbs: make(map[int]FunctionCallbackWithError),
	}
	iso.null = newValueNull(iso)
	iso.undefined = newValueUndefined(iso)
	return iso
}

// TerminateExecution terminates forcefully the current thread
// of JavaScript execution in the given isolate.
func (i *Isolate) TerminateExecution() {
	C.IsolateTerminateExecution(i.ptr)
}

// CancelTerminateExecution resumes execution capability in an isolate whose
// execution was forcefully terminated with TerminateExecution. It may resume
// the isolate even while JavaScript frames remain on the stack, so it should
// only be called after the embedder that owns the termination has handled it.
// It may be called from any thread without acquiring a V8 Locker. The caller
// must ensure the isolate remains alive and coordinate with Dispose.
func (i *Isolate) CancelTerminateExecution() {
	C.IsolateCancelTerminateExecution(i.ptr)
}

// IsExecutionTerminating returns whether V8 is currently terminating
// Javascript execution. If true, there are still JavaScript frames
// on the stack and the termination exception is still active.
func (i *Isolate) IsExecutionTerminating() bool {
	return C.IsolateIsExecutionTerminating(i.ptr) == 1
}

// GetContinuationPreservedEmbedderData returns the opaque token associated
// with the currently running continuation. V8 captures this token when it
// creates a continuation and restores it while that continuation runs.
//
// This token-only API is intentionally narrow: embedders can use the token as
// a key into Go-owned scope data without retaining arbitrary V8 values.
// Zero means that no token is set.
func (i *Isolate) GetContinuationPreservedEmbedderData() uint32 {
	return uint32(C.IsolateGetContinuationPreservedEmbedderData(i.ptr))
}

// SetContinuationPreservedEmbedderData sets the opaque token V8 will capture
// on subsequently created continuations. Setting zero clears the token.
func (i *Isolate) SetContinuationPreservedEmbedderData(token uint32) {
	C.IsolateSetContinuationPreservedEmbedderData(i.ptr, C.uint32_t(token))
}

type CompileOptions struct {
	CachedData *CompilerCachedData

	Mode CompileMode
}

// CompileUnboundScript will create an UnboundScript (i.e. context-indepdent)
// using the provided source JavaScript, origin (a.k.a. filename), and options.
// If options contain a non-null CachedData, compilation of the script will use
// that code cache.
// error will be of type `JSError` if not nil.
func (i *Isolate) CompileUnboundScript(
	source, origin string,
	opts CompileOptions,
) (*UnboundScript, error) {
	cSource := C.CString(source)
	cOrigin := C.CString(origin)
	defer C.free(unsafe.Pointer(cSource))
	defer C.free(unsafe.Pointer(cOrigin))

	var cOptions C.CompileOptions
	if opts.CachedData != nil {
		if opts.Mode != 0 {
			panic("On CompileOptions, Mode and CachedData can't both be set")
		}
		cOptions.compileOption = C.ScriptCompilerConsumeCodeCache
		cOptions.cachedData = C.ScriptCompilerCachedData{
			data:   (*C.uchar)(unsafe.Pointer(&opts.CachedData.Bytes[0])),
			length: C.int(len(opts.CachedData.Bytes)),
		}
	} else {
		cOptions.compileOption = C.int(opts.Mode)
	}

	rtn := C.IsolateCompileUnboundScript(i.ptr, cSource, cOrigin, cOptions)
	if rtn.ptr == nil {
		return nil, newJSError(rtn.error)
	}
	if opts.CachedData != nil {
		opts.CachedData.Rejected = int(rtn.cachedDataRejected) == 1
	}
	return &UnboundScript{
		ptr: rtn.ptr,
		iso: i,
	}, nil
}

// GetHeapStatistics returns heap statistics for an isolate.
func (i *Isolate) GetHeapStatistics() HeapStatistics {
	hs := C.IsolationGetHeapStatistics(i.ptr)

	return HeapStatistics{
		TotalHeapSize:            uint64(hs.total_heap_size),
		TotalHeapSizeExecutable:  uint64(hs.total_heap_size_executable),
		TotalPhysicalSize:        uint64(hs.total_physical_size),
		TotalAvailableSize:       uint64(hs.total_available_size),
		UsedHeapSize:             uint64(hs.used_heap_size),
		HeapSizeLimit:            uint64(hs.heap_size_limit),
		MallocedMemory:           uint64(hs.malloced_memory),
		ExternalMemory:           uint64(hs.external_memory),
		PeakMallocedMemory:       uint64(hs.peak_malloced_memory),
		NumberOfNativeContexts:   uint64(hs.number_of_native_contexts),
		NumberOfDetachedContexts: uint64(hs.number_of_detached_contexts),
	}
}

// Dispose will dispose the Isolate VM; subsequent calls will panic.
func (i *Isolate) Dispose() {
	if i.ptr == nil {
		return
	}
	C.IsolateDispose(i.ptr)
	i.ptr = nil
}

// ThrowException schedules an exception to be thrown when returning to
// JavaScript. When an exception has been scheduled it is illegal to invoke
// any JavaScript operation; the caller must return immediately and only after
// the exception has been handled does it become legal to invoke JavaScript operations.
func (i *Isolate) ThrowException(value *Value) *Value {
	if i.ptr == nil {
		panic("Isolate has been disposed")
	}
	return &Value{
		ptr: C.IsolateThrowException(i.ptr, value.ptr),
	}
}

// Deprecated: use `iso.Dispose()`.
func (i *Isolate) Close() {
	i.Dispose()
}

func (i *Isolate) apply(opts *contextOptions) {
	opts.iso = i
}

func (i *Isolate) registerCallback(cb FunctionCallbackWithError) int {
	i.cbMutex.Lock()
	i.cbSeq++
	ref := i.cbSeq
	i.cbs[ref] = cb
	i.cbMutex.Unlock()
	return ref
}

func (i *Isolate) getCallback(ref int) FunctionCallbackWithError {
	i.cbMutex.RLock()
	defer i.cbMutex.RUnlock()
	return i.cbs[ref]
}
