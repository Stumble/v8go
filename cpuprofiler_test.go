// Copyright 2021 the v8go contributors. All rights reserved.
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

package v8go_test

import (
	"testing"

	v8 "github.com/stumble/v8go"
)

func TestCPUProfiler_Dispose(t *testing.T) {
	t.Parallel()

	iso := v8.NewIsolate()
	defer iso.Dispose()
	cpuProfiler := v8.NewCPUProfiler(iso)

	cpuProfiler.Dispose()
	// noop when called multiple times
	cpuProfiler.Dispose()

	// verify panics when profiler disposed
	if recoverPanic(func() { cpuProfiler.StartProfiling("") }) == nil {
		t.Error("expected panic")
	}

	if recoverPanic(func() { cpuProfiler.StopProfiling("") }) == nil {
		t.Error("expected panic")
	}

	cpuProfiler = v8.NewCPUProfiler(iso)
	defer cpuProfiler.Dispose()
	iso.Dispose()

	// verify panics when isolate disposed
	if recoverPanic(func() { cpuProfiler.StartProfiling("") }) == nil {
		t.Error("expected panic")
	}

	if recoverPanic(func() { cpuProfiler.StopProfiling("") }) == nil {
		t.Error("expected panic")
	}
}

func TestCPUProfiler(t *testing.T) {
	t.Parallel()

	ctx := v8.NewContext(nil)
	iso := ctx.Isolate()
	defer iso.Dispose()
	defer ctx.Close()

	cpuProfiler := v8.NewCPUProfiler(iso)
	defer cpuProfiler.Dispose()

	title := "cpuprofilertest"
	cpuProfiler.StartProfiling(title)

	_, err := ctx.RunScript(profileScript, "script.js")
	fatalIf(t, err)
	val, err := ctx.Global().Get("start")
	fatalIf(t, err)
	fn, err := val.AsFunction()
	fatalIf(t, err)
	timeout, err := v8.NewValue(iso, int32(0))
	fatalIf(t, err)
	_, err = fn.Call(ctx.Global(), timeout)
	fatalIf(t, err)

	cpuProfile := cpuProfiler.StopProfiling(title)
	defer cpuProfile.Delete()

	if cpuProfile.GetTitle() != title {
		t.Errorf("expected %s, but got %s", title, cpuProfile.GetTitle())
	}
}

const profileScript = `function loop(timeout) {
  this.mmm = 0;
  var start = Date.now();
  while (Date.now() - start < timeout) {
    var n = 10;
    while(n > 1) {
      n--;
      this.mmm += n * n * n;
    }
  }
}
function delay(timeout = 10) { try { loop(timeout); } catch(e) { } }
function bar(timeout) { delay(timeout); }
function baz(timeout) { delay(timeout); }
function foo(delayTimeout = 10) {
    try {
       delay(delayTimeout);
       bar(delayTimeout);
       delay(delayTimeout);
       baz(delayTimeout);
    } catch (e) { }
}
function start(timeout, delayTimeout = 10) {
  var start = Date.now();
  do {
    foo(delayTimeout);
    var duration = Date.now() - start;
  } while (duration < timeout);
  return duration;
};`
