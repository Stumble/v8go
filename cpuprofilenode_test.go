// Copyright 2021 the v8go contributors. All rights reserved.
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

package v8go_test

import (
	"testing"

	v8 "github.com/stumble/v8go"
)

func TestCPUProfileNode(t *testing.T) {
	// CPU profiles are sampling based. Keep one call stack active for the
	// entire sample window instead of requiring several short-lived sibling
	// paths to all appear by chance.

	ctx := v8.NewContext(nil)
	iso := ctx.Isolate()
	defer iso.Dispose()
	defer ctx.Close()

	cpuProfiler := v8.NewCPUProfiler(iso)
	defer cpuProfiler.Dispose()

	title := "cpuprofilenodetest"
	cpuProfiler.StartProfiling(title)

	_, err := ctx.RunScript(profileNodeScript, "script.js")
	fatalIf(t, err)
	val, err := ctx.Global().Get("start")
	fatalIf(t, err)
	fn, err := val.AsFunction()
	fatalIf(t, err)
	timeout, err := v8.NewValue(iso, int32(1000))
	fatalIf(t, err)
	_, err = fn.Call(ctx.Global(), timeout)
	fatalIf(t, err)

	cpuProfile := cpuProfiler.StopProfiling(title)
	if cpuProfile == nil {
		t.Fatal("expected profile not to be nil")
	}
	defer cpuProfile.Delete()

	rootNode := cpuProfile.GetTopDownRoot()
	if rootNode == nil {
		t.Fatal("expected top down root not to be nil")
	}
	loopNode := findDescendant(t, rootNode, "loop")
	checkNode(t, loopNode, "script.js", "loop", 1, 14)

	delayNode := loopNode.GetParent()
	checkNode(t, delayNode, "script.js", "delay", 12, 15)
	fooNode := delayNode.GetParent()
	checkNode(t, fooNode, "script.js", "foo", 13, 13)
	startNode := fooNode.GetParent()
	checkNode(t, startNode, "script.js", "start", 14, 15)
	if parentName := startNode.GetParent().GetFunctionName(); parentName != "(root)" {
		t.Fatalf("expected (root), but got %v", parentName)
	}
}

func findDescendant(t *testing.T, node *v8.CPUProfileNode, functionName string) *v8.CPUProfileNode {
	t.Helper()

	for i := 0; i < node.GetChildrenCount(); i++ {
		child := node.GetChild(i)
		if child.GetFunctionName() == functionName {
			return child
		}
		if descendant := findDescendantOrNil(child, functionName); descendant != nil {
			return descendant
		}
	}
	t.Fatalf("failed to find descendant node %q", functionName)
	return nil
}

func findDescendantOrNil(node *v8.CPUProfileNode, functionName string) *v8.CPUProfileNode {
	for i := 0; i < node.GetChildrenCount(); i++ {
		child := node.GetChild(i)
		if child.GetFunctionName() == functionName {
			return child
		}
		if descendant := findDescendantOrNil(child, functionName); descendant != nil {
			return descendant
		}
	}
	return nil
}

const profileNodeScript = `function loop(timeout) {
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
function delay(timeout) { loop(timeout); }
function foo(timeout) { delay(timeout); }
function start(timeout) { foo(timeout); }`

func checkNode(t *testing.T, node *v8.CPUProfileNode, scriptResourceName string, functionName string, line, column int) {
	t.Helper()

	if node.GetFunctionName() != functionName {
		t.Fatalf("expected node to have function name %s, but got %s", functionName, node.GetFunctionName())
	}
	if node.GetScriptResourceName() != scriptResourceName {
		t.Fatalf("expected node to have script resource name %s, but got %s", scriptResourceName, node.GetScriptResourceName())
	}
	if node.GetLineNumber() != line {
		t.Fatalf("expected node at line %d, but got %d", line, node.GetLineNumber())
	}
	if node.GetColumnNumber() != column {
		t.Fatalf("expected node at column %d, but got %d", column, node.GetColumnNumber())
	}
}
