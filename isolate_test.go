// Copyright 2019 Roger Chapman and the v8go contributors. All rights reserved.
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

package v8go_test

import (
	"encoding/json"
	"fmt"
	"math/rand"
	"strings"
	"testing"
	"time"

	v8 "github.com/stumble/v8go"
)

const isolateTerminationTestTimeout = 2 * time.Second

type isolateTerminationWatchResult struct {
	started  bool
	timedOut bool
	stopped  bool
}

func startIsolateTerminationWatchdog(
	iso *v8.Isolate,
	started <-chan struct{},
	stop <-chan struct{},
) <-chan isolateTerminationWatchResult {
	done := make(chan isolateTerminationWatchResult, 1)
	go func() {
		timer := time.NewTimer(isolateTerminationTestTimeout)
		defer timer.Stop()

		result := isolateTerminationWatchResult{}
		select {
		case <-started:
			result.started = true
			iso.TerminateExecution()
		case <-timer.C:
			result.timedOut = true
			iso.TerminateExecution()
		case <-stop:
			result.stopped = true
		}
		done <- result
	}()
	return done
}

func TestIsolateTerminateExecution(t *testing.T) {
	t.Parallel()
	iso := v8.NewIsolate()
	defer iso.Dispose()

	if iso.IsExecutionTerminating() {
		t.Error("expected no execution to be terminating")
	}

	var terminating bool
	fooFn := v8.NewFunctionTemplate(iso, func(info *v8.FunctionCallbackInfo) *v8.Value {
		loop, _ := info.Args()[0].AsFunction()
		go func() {
			iso.TerminateExecution()
		}()
		loop.Call(v8.Undefined(iso))

		terminating = iso.IsExecutionTerminating()
		return nil
	})

	global := v8.NewObjectTemplate(iso)
	global.Set("foo", fooFn)

	ctx := v8.NewContext(iso, global)
	defer ctx.Close()

	script := `function loop() { while (true) { } }; foo(loop);`
	_, e := ctx.RunScript(script, "forever.js")
	if e == nil || !strings.HasPrefix(e.Error(), "ExecutionTerminated") {
		t.Errorf("unexpected error: %v", e)
	}

	if !terminating {
		t.Error("expected execution to have been terminating in function")
	}
}

func TestIsolateCancelTerminateExecutionRecoversParentAndChild(t *testing.T) {
	iso := v8.NewIsolate()
	defer iso.Dispose()

	var childStarted chan<- struct{}
	childGlobal := v8.NewObjectTemplate(iso)
	childGlobal.Set("started", v8.NewFunctionTemplate(iso, func(*v8.FunctionCallbackInfo) *v8.Value {
		select {
		case childStarted <- struct{}{}:
		default:
		}
		return nil
	}))
	child := v8.NewContext(iso, childGlobal)
	defer child.Close()

	type cycleResult struct {
		childErr                error
		watch                   isolateTerminationWatchResult
		watchJoinTimedOut       bool
		terminatingBeforeCancel bool
		terminatingAfterCancel  bool
		childRecoveryValue      int64
		childRecoveryErr        error
	}
	var cycles []cycleResult

	parentGlobal := v8.NewObjectTemplate(iso)
	parentGlobal.Set("runChild", v8.NewFunctionTemplate(iso, func(*v8.FunctionCallbackInfo) *v8.Value {
		started := make(chan struct{}, 1)
		stop := make(chan struct{})
		childStarted = started
		watchDone := startIsolateTerminationWatchdog(iso, started, stop)

		result := cycleResult{}
		_, result.childErr = child.RunScript("started(); while (true) {}", "child.js")
		childStarted = nil
		close(stop)

		joinTimer := time.NewTimer(isolateTerminationTestTimeout)
		select {
		case result.watch = <-watchDone:
			if !joinTimer.Stop() {
				select {
				case <-joinTimer.C:
				default:
				}
			}
		case <-joinTimer.C:
			result.watchJoinTimedOut = true
		}
		if result.watchJoinTimedOut {
			cycles = append(cycles, result)
			return nil
		}

		result.terminatingBeforeCancel = iso.IsExecutionTerminating()
		iso.CancelTerminateExecution()
		result.terminatingAfterCancel = iso.IsExecutionTerminating()

		value, err := child.RunScript("40 + 2", "child-recovered-in-callback.js")
		result.childRecoveryErr = err
		if err == nil {
			result.childRecoveryValue = value.Integer()
		}
		cycles = append(cycles, result)
		return nil
	}))
	parent := v8.NewContext(iso, parentGlobal)
	defer parent.Close()
	sibling := v8.NewContext(iso)
	defer sibling.Close()

	for cycle := 0; cycle < 3; cycle++ {
		value, parentErr := parent.RunScript("runChild(); 40 + 2", fmt.Sprintf("parent-cycle-%d.js", cycle))
		if len(cycles) != cycle+1 {
			t.Fatalf("cycle %d: parent callback did not complete", cycle)
		}
		result := cycles[cycle]
		if result.watchJoinTimedOut {
			t.Fatalf("cycle %d: termination watcher did not join", cycle)
		}
		if result.watch.timedOut {
			t.Fatalf("cycle %d: watchdog fired before child signalled start", cycle)
		}
		if result.watch.stopped || !result.watch.started {
			t.Fatalf("cycle %d: child start handshake did not complete: %+v", cycle, result.watch)
		}
		if result.childErr == nil || !strings.HasPrefix(result.childErr.Error(), "ExecutionTerminated") {
			t.Fatalf("cycle %d: unexpected child error: %v", cycle, result.childErr)
		}
		if !result.terminatingBeforeCancel {
			t.Fatalf("cycle %d: expected active termination while parent JavaScript frame remained", cycle)
		}
		if result.terminatingAfterCancel {
			t.Fatalf("cycle %d: expected CancelTerminateExecution to clear termination", cycle)
		}
		if result.childRecoveryErr != nil {
			t.Fatalf("cycle %d: child did not recover inside parent callback: %v", cycle, result.childRecoveryErr)
		}
		if result.childRecoveryValue != 42 {
			t.Fatalf("cycle %d: child returned %d inside parent callback, want 42", cycle, result.childRecoveryValue)
		}
		if parentErr != nil {
			t.Fatalf("cycle %d: parent JavaScript did not continue after callback: %v", cycle, parentErr)
		}
		if got := value.Integer(); got != 42 {
			t.Fatalf("cycle %d: parent returned %d, want 42", cycle, got)
		}

		value, err := child.RunScript("6 * 7", "child-recovered.js")
		if err != nil {
			t.Fatalf("cycle %d: child context did not remain reusable: %v", cycle, err)
		}
		if got := value.Integer(); got != 42 {
			t.Fatalf("cycle %d: child context returned %d, want 42", cycle, got)
		}

		value, err = sibling.RunScript("6 * 7", "sibling-recovered.js")
		if err != nil {
			t.Fatalf("cycle %d: sibling context did not recover: %v", cycle, err)
		}
		if got := value.Integer(); got != 42 {
			t.Fatalf("cycle %d: sibling context returned %d, want 42", cycle, got)
		}
	}
}

func TestIsolateCompileUnboundScript(t *testing.T) {
	s := "function foo() { return 'bar'; }; foo()"

	i1 := v8.NewIsolate()
	defer i1.Dispose()
	c1 := v8.NewContext(i1)
	defer c1.Close()

	_, err := i1.CompileUnboundScript("invalid js", "filename", v8.CompileOptions{})
	if err == nil {
		t.Fatal("expected error")
	}

	us, err := i1.CompileUnboundScript(s, "script.js", v8.CompileOptions{Mode: v8.CompileModeEager})
	fatalIf(t, err)

	val, err := us.Run(c1)
	fatalIf(t, err)
	if val.String() != "bar" {
		t.Fatalf("invalid value returned, expected bar got %v", val)
	}

	cachedData := us.CreateCodeCache()

	i2 := v8.NewIsolate()
	defer i2.Dispose()
	c2 := v8.NewContext(i2)
	defer c2.Close()

	opts := v8.CompileOptions{CachedData: cachedData}
	usWithCachedData, err := i2.CompileUnboundScript(s, "script.js", opts)
	fatalIf(t, err)
	if usWithCachedData == nil {
		t.Fatal("expected unbound script from cached data not to be nil")
	}
	if opts.CachedData.Rejected {
		t.Fatal("expected cached data to be used, not rejected")
	}

	val, err = usWithCachedData.Run(c2)
	fatalIf(t, err)
	if val.String() != "bar" {
		t.Fatalf("invalid value returned, expected bar got %v", val)
	}
}

func TestIsolateCompileUnboundScript_CachedDataRejected(t *testing.T) {
	s := "function foo() { return 'bar'; }; foo()"
	iso := v8.NewIsolate()
	defer iso.Dispose()

	// Try to compile an unbound script using cached data that does not match this source
	opts := v8.CompileOptions{CachedData: &v8.CompilerCachedData{Bytes: []byte("Math.sqrt(4)")}}
	us, err := iso.CompileUnboundScript(s, "script.js", opts)
	fatalIf(t, err)
	if !opts.CachedData.Rejected {
		t.Error("expected cached data to be rejected")
	}

	ctx := v8.NewContext(iso)
	defer ctx.Close()

	// Verify that unbound script is still compiled and able to be used
	val, err := us.Run(ctx)
	fatalIf(t, err)
	if val.String() != "bar" {
		t.Errorf("invalid value returned, expected bar got %v", val)
	}
}

func TestIsolateCompileUnboundScript_InvalidOptions(t *testing.T) {
	iso := v8.NewIsolate()
	defer iso.Dispose()

	opts := v8.CompileOptions{
		CachedData: &v8.CompilerCachedData{Bytes: []byte("unused")},
		Mode:       v8.CompileModeEager,
	}
	panicErr := recoverPanic(func() { iso.CompileUnboundScript("console.log(1)", "script.js", opts) })
	if panicErr == nil {
		t.Error("expected panic")
	}
	if panicErr != "On CompileOptions, Mode and CachedData can't both be set" {
		t.Errorf("unexpected panic: %v\n", panicErr)
	}
}

func TestIsolateGetHeapStatistics(t *testing.T) {
	t.Parallel()
	iso := v8.NewIsolate()
	defer iso.Dispose()
	ctx1 := v8.NewContext(iso)
	defer ctx1.Close()
	ctx2 := v8.NewContext(iso)
	defer ctx2.Close()

	hs := iso.GetHeapStatistics()

	if hs.NumberOfNativeContexts != 3 {
		t.Error("expect NumberOfNativeContexts return 3, got", hs.NumberOfNativeContexts)
	}

	if hs.NumberOfDetachedContexts != 0 {
		t.Error("expect NumberOfDetachedContexts return 0, got", hs.NumberOfDetachedContexts)
	}
}

func TestCallbackRegistry(t *testing.T) {
	t.Parallel()

	iso := v8.NewIsolate()
	defer iso.Dispose()
	cb := func(*v8.FunctionCallbackInfo) (*v8.Value, error) { return nil, nil }

	cb0 := iso.GetCallback(0)
	if cb0 != nil {
		t.Error("expected callback function to be <nil>")
	}
	ref1 := iso.RegisterCallback(cb)
	if ref1 != 1 {
		t.Errorf("expected callback ref == 1, got %d", ref1)
	}
	cb1 := iso.GetCallback(1)
	if fmt.Sprintf("%p", cb1) != fmt.Sprintf("%p", cb) {
		t.Errorf("unexpected callback function; want %p, got %p", cb, cb1)
	}
}

func TestIsolateDispose(t *testing.T) {
	t.Parallel()

	iso := v8.NewIsolate()
	if iso.GetHeapStatistics().TotalHeapSize == 0 {
		t.Error("Isolate incorrectly allocated")
	}

	iso.Dispose()
	// noop when called multiple times
	iso.Dispose()
	// deprecated
	iso.Close()

	if iso.GetHeapStatistics().TotalHeapSize != 0 {
		t.Error("Isolate not disposed correctly")
	}
}

func TestIsolateThrowException(t *testing.T) {
	t.Parallel()
	iso := v8.NewIsolate()

	strErr, _ := v8.NewValue(iso, "some type error")

	throwError := func(val *v8.Value) {
		v := iso.ThrowException(val)

		if !v.IsNullOrUndefined() {
			t.Error("expected result to be null or undefined")
		}
	}

	// Function that throws a simple string error from within the function. It is meant
	// to emulate when an error is returned within Go.
	fn := v8.NewFunctionTemplate(iso, func(info *v8.FunctionCallbackInfo) *v8.Value {
		throwError(strErr)

		return nil
	})

	// Function that is passed a TypeError from JavaScript.
	fn2 := v8.NewFunctionTemplate(iso, func(info *v8.FunctionCallbackInfo) *v8.Value {
		typeErr := info.Args()[0]

		throwError(typeErr)

		return nil
	})

	global := v8.NewObjectTemplate(iso)
	global.Set("foo", fn)
	global.Set("foo2", fn2)

	ctx := v8.NewContext(iso, global)

	_, e := ctx.RunScript("foo()", "foo.js")

	if e.Error() != "some type error" {
		t.Errorf("expected \"some type error\" error but got: %v", e)
	}

	_, e = ctx.RunScript("foo2(new TypeError('this is a test'))", "foo.js")

	if e.Error() != "TypeError: this is a test" {
		t.Errorf("expected \"TypeError: this is a test\" error but got: %v", e)
	}

	ctx.Close()
	iso.Dispose()
	if recoverPanic(func() { iso.ThrowException(strErr) }) == nil {
		t.Error("expected panic")
	}
}

func BenchmarkIsolateInitialization(b *testing.B) {
	b.ReportAllocs()
	for n := 0; n < b.N; n++ {
		vm := v8.NewIsolate()
		vm.Close() // force disposal of the VM
	}
}

func BenchmarkIsolateInitAndRun(b *testing.B) {
	b.ReportAllocs()
	for n := 0; n < b.N; n++ {
		vm := v8.NewIsolate()
		ctx := v8.NewContext(vm)
		ctx.RunScript(script, "main.js")
		str, _ := json.Marshal(makeObject())
		cmd := fmt.Sprintf("process(%s)", str)
		ctx.RunScript(cmd, "cmd.js")
		ctx.Close()
		vm.Close() // force disposal of the VM
	}
}

const script = `
	const process = (record) => {
		const res = [];
		for (let [k, v] of Object.entries(record)) {
			res.push({
				name: k,
				value: v,
			});
		}
		return JSON.stringify(res);
	};
`

func makeObject() interface{} {
	return map[string]interface{}{
		"a": rand.Intn(1000000),
		"b": "AAAABBBBAAAABBBBAAAABBBBAAAABBBBAAAABBBB",
	}
}

func TestNewIsolateWithConstraints(t *testing.T) {
	t.Parallel()

	iso := v8.NewIsolate(v8.WithResourceConstraints(
		8*1024*1024,
		16*1024*1024,
	))
	defer iso.Dispose()

	ctx := v8.NewContext(iso)
	defer ctx.Close()

	// First test - should work fine
	val, err := ctx.RunScript("1 + 2", "test.js")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if !val.IsNumber() || val.Number() != 3 {
		t.Errorf("expected 3, got %v", val)
	}

	// Second test - should run out of memory without crashing the process
	val, err = ctx.RunScript(`
			const data = [];
			for (let i = 0; i < 1000 * 1000; i++) {
					data.push("large data chunk ".repeat(1000));
			}
			data.length;
		`, "memory-test.js")
	if err != nil {
		t.Logf("Memory test correctly returned error: %v", err)
	} else {
		t.Fatalf("Memory test completed unexpectedly: %v", val)
	}
}
