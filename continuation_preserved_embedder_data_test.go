// Copyright 2026 The v8go contributors. All rights reserved.
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

package v8go_test

import (
	"testing"

	v8 "github.com/stumble/v8go"
)

type continuationObservation struct {
	label string
	token uint32
}

type continuationHarness struct {
	t            *testing.T
	iso          *v8.Isolate
	parent       *v8.Context
	child        *v8.Context
	observations []continuationObservation
}

func newContinuationHarness(t *testing.T) *continuationHarness {
	t.Helper()

	h := &continuationHarness{t: t, iso: v8.NewIsolate()}
	t.Cleanup(h.iso.Dispose)

	probe := v8.NewFunctionTemplate(h.iso, func(info *v8.FunctionCallbackInfo) *v8.Value {
		label := ""
		if args := info.Args(); len(args) == 1 {
			label = args[0].String()
		}
		h.observations = append(h.observations, continuationObservation{
			label: label,
			token: h.iso.GetContinuationPreservedEmbedderData(),
		})
		return nil
	})

	parentGlobal := v8.NewObjectTemplate(h.iso)
	fatalIf(t, parentGlobal.Set("nativeProbe", probe))
	h.parent = v8.NewContext(h.iso, parentGlobal)
	t.Cleanup(h.parent.Close)

	h.child = v8.NewContext(h.iso)
	t.Cleanup(h.child.Close)

	value, err := h.parent.RunScript(`
		globalThis.parentHelper = function parentHelper(label) {
			nativeProbe(label);
		};
	`, "parent-helper.js")
	fatalIf(t, err)
	value.Release()

	parentHelper, err := h.parent.Global().Get("parentHelper")
	fatalIf(t, err)
	fatalIf(t, h.child.Global().Set("parentHelper", parentHelper))

	return h
}

func (h *continuationHarness) installParentHelper(name string, source string) {
	h.t.Helper()
	value, err := h.parent.RunScript(source, name+".js")
	fatalIf(h.t, err)
	value.Release()

	helper, err := h.parent.Global().Get(name)
	fatalIf(h.t, err)
	fatalIf(h.t, h.child.Global().Set(name, helper))
}

func (h *continuationHarness) requireToken(label string, want uint32) {
	h.t.Helper()
	for _, observation := range h.observations {
		if observation.label == label {
			if observation.token != want {
				h.t.Errorf(
					"%s continuation token = %d, want %d",
					label,
					observation.token,
					want,
				)
			}
			return
		}
	}
	h.t.Errorf("missing callback observation %q in %+v", label, h.observations)
}

func withContinuationToken(t *testing.T, iso *v8.Isolate, token uint32, fn func()) {
	t.Helper()
	previous := iso.GetContinuationPreservedEmbedderData()
	iso.SetContinuationPreservedEmbedderData(token)
	defer func() {
		iso.SetContinuationPreservedEmbedderData(previous)
		if restored := iso.GetContinuationPreservedEmbedderData(); restored != previous {
			t.Errorf("restored continuation token = %d, want %d", restored, previous)
		}
	}()
	fn()
}

func TestContinuationPreservedEmbedderDataGetSet(t *testing.T) {
	iso := v8.NewIsolate()
	defer iso.Dispose()

	if got := iso.GetContinuationPreservedEmbedderData(); got != 0 {
		t.Fatalf("initial continuation token = %d, want 0", got)
	}

	for _, token := range []uint32{42, ^uint32(0)} {
		iso.SetContinuationPreservedEmbedderData(token)
		if got := iso.GetContinuationPreservedEmbedderData(); got != token {
			t.Fatalf("continuation token = %d, want %d", got, token)
		}
	}

	iso.SetContinuationPreservedEmbedderData(0)
	if got := iso.GetContinuationPreservedEmbedderData(); got != 0 {
		t.Fatalf("cleared continuation token = %d, want 0", got)
	}
}

func TestContinuationPreservedEmbedderDataCrossRealmAsyncPropagation(t *testing.T) {
	const (
		parentToken = uint32(101)
		childToken  = uint32(202)
	)

	t.Run("synchronous child entry", func(t *testing.T) {
		h := newContinuationHarness(t)
		h.iso.SetContinuationPreservedEmbedderData(parentToken)
		t.Cleanup(func() { h.iso.SetContinuationPreservedEmbedderData(0) })

		withContinuationToken(t, h.iso, childToken, func() {
			value, err := h.child.RunScript(
				`parentHelper("sync")`,
				"child-sync.js",
			)
			fatalIf(t, err)
			value.Release()
		})

		h.requireToken("sync", childToken)
		if got := h.iso.GetContinuationPreservedEmbedderData(); got != parentToken {
			t.Fatalf("post-child token = %d, want parent token %d", got, parentToken)
		}
	})

	t.Run("foreign helper await immediate value", func(t *testing.T) {
		h := newContinuationHarness(t)
		h.iso.SetContinuationPreservedEmbedderData(parentToken)
		t.Cleanup(func() { h.iso.SetContinuationPreservedEmbedderData(0) })
		h.installParentHelper("asyncParentHelper", `
			globalThis.asyncParentHelper = async function asyncParentHelper(label) {
				await 0;
				nativeProbe(label);
			};
		`)

		withContinuationToken(t, h.iso, childToken, func() {
			value, err := h.child.RunScript(`
				(async () => {
					await asyncParentHelper("foreign-await");
				})();
			`, "child-awaits-parent-helper.js")
			fatalIf(t, err)
			value.Release()
		})
		h.child.PerformMicrotaskCheckpoint()

		h.requireToken("foreign-await", childToken)
		if got := h.iso.GetContinuationPreservedEmbedderData(); got != parentToken {
			t.Fatalf("post-await token = %d, want parent token %d", got, parentToken)
		}
	})

	t.Run("foreign helper await later host promise", func(t *testing.T) {
		h := newContinuationHarness(t)
		h.iso.SetContinuationPreservedEmbedderData(parentToken)
		t.Cleanup(func() { h.iso.SetContinuationPreservedEmbedderData(0) })
		resolver, err := v8.NewPromiseResolver(h.parent)
		fatalIf(t, err)
		fatalIf(t, h.parent.Global().Set("hostPromise", resolver.GetPromise()))
		h.installParentHelper("hostAsyncParentHelper", `
			globalThis.hostAsyncParentHelper = async function hostAsyncParentHelper(label) {
				await hostPromise;
				nativeProbe(label);
			};
		`)

		withContinuationToken(t, h.iso, childToken, func() {
			value, runErr := h.child.RunScript(`
				(async () => {
					await hostAsyncParentHelper("host-promise");
				})();
			`, "child-awaits-host-parent-helper.js")
			fatalIf(t, runErr)
			value.Release()
		})
		if !resolver.Resolve(v8.Undefined(h.iso)) {
			t.Fatal("host Promise did not resolve")
		}
		h.child.PerformMicrotaskCheckpoint()

		h.requireToken("host-promise", childToken)
		if got := h.iso.GetContinuationPreservedEmbedderData(); got != parentToken {
			t.Fatalf("post-host-settlement token = %d, want parent token %d", got, parentToken)
		}
	})

	t.Run("fire and forget foreign helper", func(t *testing.T) {
		h := newContinuationHarness(t)
		h.iso.SetContinuationPreservedEmbedderData(parentToken)
		t.Cleanup(func() { h.iso.SetContinuationPreservedEmbedderData(0) })
		resolver, err := v8.NewPromiseResolver(h.parent)
		fatalIf(t, err)
		fatalIf(t, h.parent.Global().Set("hostPromise", resolver.GetPromise()))
		h.installParentHelper("fireAndForgetParentHelper", `
			globalThis.fireAndForgetParentHelper = async function fireAndForgetParentHelper(label) {
				await hostPromise;
				nativeProbe(label);
			};
		`)

		withContinuationToken(t, h.iso, childToken, func() {
			value, runErr := h.child.RunScript(`
				fireAndForgetParentHelper("fire-and-forget");
				"child-returned";
			`, "child-fire-and-forget.js")
			fatalIf(t, runErr)
			value.Release()
		})
		if !resolver.Resolve(v8.Undefined(h.iso)) {
			t.Fatal("host Promise did not resolve")
		}
		h.child.PerformMicrotaskCheckpoint()

		h.requireToken("fire-and-forget", childToken)
		if got := h.iso.GetContinuationPreservedEmbedderData(); got != parentToken {
			t.Fatalf("post-fire-and-forget token = %d, want parent token %d", got, parentToken)
		}
	})
}

func TestContinuationPreservedEmbedderDataSeparatesJobsInOneCheckpoint(t *testing.T) {
	const (
		parentToken = uint32(101)
		childToken  = uint32(202)
		drainToken  = uint32(303)
	)
	h := newContinuationHarness(t)

	withContinuationToken(t, h.iso, parentToken, func() {
		value, err := h.parent.RunScript(`
			globalThis.resolveParentJob = undefined;
			new Promise((resolve) => { globalThis.resolveParentJob = resolve; })
				.then(() => parentHelper("parent-job"));
		`, "parent-job-registration.js")
		fatalIf(t, err)
		value.Release()
	})

	withContinuationToken(t, h.iso, childToken, func() {
		value, err := h.child.RunScript(`
			globalThis.resolveChildJob = undefined;
			new Promise((resolve) => { globalThis.resolveChildJob = resolve; })
				.then(() => parentHelper("child-job"));
		`, "child-job-registration.js")
		fatalIf(t, err)
		value.Release()
	})

	resolveChildJob, err := h.child.Global().Get("resolveChildJob")
	fatalIf(t, err)
	fatalIf(t, h.parent.Global().Set("resolveChildJob", resolveChildJob))

	withContinuationToken(t, h.iso, drainToken, func() {
		value, runErr := h.parent.RunScript(`
			resolveParentJob();
			resolveChildJob();
		`, "resolve-both-jobs.js")
		fatalIf(t, runErr)
		value.Release()
		h.parent.PerformMicrotaskCheckpoint()
	})

	h.requireToken("parent-job", parentToken)
	h.requireToken("child-job", childToken)
	if got := h.iso.GetContinuationPreservedEmbedderData(); got != 0 {
		t.Fatalf("post-checkpoint token = %d, want 0", got)
	}
}

func TestContinuationPreservedEmbedderDataNestedRestore(t *testing.T) {
	const (
		parentToken = uint32(101)
		childToken  = uint32(202)
	)
	h := newContinuationHarness(t)
	h.iso.SetContinuationPreservedEmbedderData(parentToken)
	t.Cleanup(func() { h.iso.SetContinuationPreservedEmbedderData(0) })

	value, err := h.child.RunScript(`
		globalThis.childNested = function childNested() {
			parentHelper("nested-child");
		};
	`, "child-nested.js")
	fatalIf(t, err)
	value.Release()
	childNested, err := h.child.Global().Get("childNested")
	fatalIf(t, err)
	fatalIf(t, h.parent.Global().Set("childNested", childNested))

	value, err = h.parent.RunScript(`
		globalThis.parentNestedHelper = function parentNestedHelper() {
			nativeProbe("nested-before");
			childNested();
			nativeProbe("nested-after");
		};
	`, "parent-nested-helper.js")
	fatalIf(t, err)
	value.Release()
	parentNestedHelper, err := h.parent.Global().Get("parentNestedHelper")
	fatalIf(t, err)
	fatalIf(t, h.child.Global().Set("parentNestedHelper", parentNestedHelper))

	withContinuationToken(t, h.iso, childToken, func() {
		childValue, runErr := h.child.RunScript(
			`parentNestedHelper()`,
			"child-nested-entry.js",
		)
		fatalIf(t, runErr)
		childValue.Release()
	})
	h.requireToken("nested-before", childToken)
	h.requireToken("nested-child", childToken)
	h.requireToken("nested-after", childToken)

	value, err = h.parent.RunScript(`parentHelper("restored-parent")`, "parent-restored.js")
	fatalIf(t, err)
	value.Release()
	h.requireToken("restored-parent", parentToken)
}
