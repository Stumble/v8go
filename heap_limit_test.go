// Copyright 2019 Roger Chapman and the v8go contributors. All rights reserved.
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

package v8go_test

import (
	"os"
	"os/exec"
	"strings"
	"testing"

	v8 "github.com/stumble/v8go"
)

// heapHogScript retains enough ordinary heap objects to exhaust any small
// ceiling. Plain strings, not TypedArrays: an ArrayBuffer's backing store is
// allocated outside the bounded heap and would never reach the limit.
//
// The allocations are deliberately confined to an IIFE. A top-level `const`
// lands in the script's lexical scope, which the Context keeps reachable, so the
// memory would still be held after the script was terminated — see
// TestHeapLimitStaysRaisedWhileTheMemoryIsStillHeld.
const heapHogScript = `
	(() => {
		const data = [];
		for (let i = 0; i < 1000 * 1000; i++) {
			data.push("large data chunk ".repeat(1000));
		}
		return data.length;
	})();
`

// heapHogRetainedScript is the same allocation, kept reachable from the script's
// lexical scope on purpose.
const heapHogRetainedScript = `
	const retained = [];
	for (let i = 0; i < 1000 * 1000; i++) {
		retained.push("large data chunk ".repeat(1000));
	}
	retained.length;
`

// churnScript allocates a little and drops it, to give V8 the collections it
// needs to notice the heap has drained.
const churnScript = `(() => { const t = []; for (let i = 0; i < 200000; i++) t.push({ i }); return t.length; })()`

const heapLimitChildEnv = "V8GO_HEAP_LIMIT_CHILD"

// Reaching the heap limit WITHOUT the option must end the process, because that
// is V8's own behaviour and this library no longer silently replaces it. An
// embedder whose main isolate exceeds the memory it was configured for generally
// wants exactly this: die and be restarted, rather than continue degraded.
//
// It has to run in a child process, since the thing under test is the parent
// dying. A test that could assert this in-process would be asserting the
// opposite of what it claims.
func TestHeapLimitWithoutOptionEndsTheProcess(t *testing.T) {
	if os.Getenv(heapLimitChildEnv) == "1" {
		iso := v8.NewIsolate(v8.WithResourceConstraints(8*1024*1024, 16*1024*1024))
		ctx := v8.NewContext(iso)
		_, _ = ctx.RunScript(heapHogScript, "oom.js")
		// Reaching this line means V8 did not end the process, which is the
		// failure this test exists to catch. Exit 0 so the parent sees success
		// where it expects a crash.
		os.Exit(0)
	}

	cmd := exec.Command(os.Args[0], "-test.run=TestHeapLimitWithoutOptionEndsTheProcess", "-test.v")
	cmd.Env = append(os.Environ(), heapLimitChildEnv+"=1")
	output, err := cmd.CombinedOutput()
	if err == nil {
		t.Fatalf("expected the child to be killed by V8's heap limit, but it exited cleanly:\n%s", output)
	}
	// "It died" is not the assertion. A child that failed to load V8, panicked in
	// the harness, or crashed for any unrelated reason also exits non-zero, and
	// this test would then pass while proving nothing. V8 prints its own fatal
	// banner before ending the process; require it.
	// The reason following this banner is V8-version-specific (for example,
	// "Reached heap limit" or "CALL_AND_RETRY_LAST"). The fatal OOM banner is
	// the stable signal that distinguishes the expected V8 failure from an
	// unrelated child-process error.
	if !strings.Contains(string(output), "Fatal JavaScript out of memory") {
		t.Fatalf("child died (%v) but not from V8's heap limit; output was:\n%s", err, output)
	}
	t.Logf("child died from V8's heap limit as expected: %v", err)
}

// With the option, the same allocation must stop the SCRIPT and leave the
// process — and every other isolate in it — running.
func TestHeapLimitWithOptionTerminatesOnlyTheScript(t *testing.T) {
	t.Parallel()

	bystander := v8.NewIsolate()
	defer bystander.Dispose()
	bystanderCtx := v8.NewContext(bystander)
	defer bystanderCtx.Close()

	iso := v8.NewIsolate(
		v8.WithResourceConstraints(8*1024*1024, 16*1024*1024),
		v8.WithTerminateOnHeapLimit(),
	)
	defer iso.Dispose()
	ctx := v8.NewContext(iso)
	defer ctx.Close()

	if _, err := ctx.RunScript(heapHogScript, "oom.js"); err == nil {
		t.Fatal("expected the heap-hogging script to fail")
	}

	// The point of the option: everything else is untouched.
	val, err := bystanderCtx.RunScript("6 * 7", "bystander.js")
	if err != nil {
		t.Fatalf("an unrelated isolate must survive: %v", err)
	}
	if !val.IsNumber() || val.Number() != 42 {
		t.Errorf("expected 42, got %v", val)
	}
}

// The callback cannot refuse to raise the limit — V8 allocates while unwinding,
// and returning the initial limit there crashes the VM — so an isolate that has
// tripped is briefly over its ceiling. AutomaticallyRestoreInitialHeapLimit is
// what puts the configured value back; without it the isolate keeps the doubled
// ceiling for life and each further trip doubles it again.
func TestHeapLimitIsRestoredAfterTermination(t *testing.T) {
	t.Parallel()

	const maxHeap = 16 * 1024 * 1024
	iso := v8.NewIsolate(
		v8.WithResourceConstraints(8*1024*1024, maxHeap),
		v8.WithTerminateOnHeapLimit(),
	)
	defer iso.Dispose()
	ctx := v8.NewContext(iso)
	defer ctx.Close()

	before := iso.GetHeapStatistics().HeapSizeLimit

	if _, err := ctx.RunScript(heapHogScript, "oom.js"); err == nil {
		t.Fatal("expected the heap-hogging script to fail")
	}

	if raised := iso.GetHeapStatistics().HeapSizeLimit; raised <= before {
		t.Fatalf("expected the callback to raise the ceiling to unwind: before=%d raised=%d", before, raised)
	}

	// The restore is tied to a GC observing the heap below the threshold, so it
	// is not instantaneous.
	for i := 0; i < 40 && iso.GetHeapStatistics().HeapSizeLimit > before; i++ {
		if _, err := ctx.RunScript(churnScript, "churn.js"); err != nil {
			break
		}
	}

	after := iso.GetHeapStatistics().HeapSizeLimit
	if after > before {
		t.Errorf("heap limit stayed raised after the terminated script's memory was collected: before=%d after=%d", before, after)
	}
}

// The counterpart, and the reason the restore is not unconditional: when the
// terminated script's allocations are still REACHABLE — a top-level `const`
// lives in the script's lexical scope, which the Context holds — the heap never
// drops below the threshold and the ceiling stays raised.
//
// That is correct rather than a gap. The isolate really is holding that memory;
// restoring the limit under it would just mean tripping again immediately. This
// test pins the distinction so the behaviour is not mistaken for the bug it
// resembles.
func TestHeapLimitStaysRaisedWhileTheMemoryIsStillHeld(t *testing.T) {
	t.Parallel()

	iso := v8.NewIsolate(
		v8.WithResourceConstraints(8*1024*1024, 16*1024*1024),
		v8.WithTerminateOnHeapLimit(),
	)
	defer iso.Dispose()
	ctx := v8.NewContext(iso)
	defer ctx.Close()

	before := iso.GetHeapStatistics().HeapSizeLimit
	if _, err := ctx.RunScript(heapHogRetainedScript, "oom-retained.js"); err == nil {
		t.Fatal("expected the heap-hogging script to fail")
	}
	for i := 0; i < 40; i++ {
		if _, err := ctx.RunScript(churnScript, "churn.js"); err != nil {
			break
		}
	}

	stats := iso.GetHeapStatistics()
	if stats.HeapSizeLimit <= before {
		t.Errorf("expected the ceiling to stay raised while %d bytes are still held", stats.UsedHeapSize)
	}
	if stats.UsedHeapSize < before/2 {
		t.Errorf("this test only means something while the memory is retained; used=%d", stats.UsedHeapSize)
	}
}
