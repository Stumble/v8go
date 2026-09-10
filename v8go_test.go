// Copyright 2019 Roger Chapman and the v8go contributors. All rights reserved.
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

package v8go_test

import (
	"os"
	"os/exec"
	"regexp"
	"testing"

	v8 "github.com/stumble/v8go"
)

func TestVersion(t *testing.T) {
	t.Parallel()
	rgx := regexp.MustCompile(`^\d+\.\d+\.\d+\.\d+-v8go$`)
	v := v8.Version()
	if !rgx.MatchString(v) {
		t.Errorf("version string is in the incorrect format: %s", v)
	}
	if expected := os.Getenv("V8GO_EXPECTED_VERSION"); expected != "" && v != expected+"-v8go" {
		t.Errorf("unexpected V8 version: got %s, want %s-v8go", v, expected)
	}
}

func TestSetFlag(t *testing.T) {
	t.Parallel()
	cmd := exec.Command(os.Args[0], "-test.run=^TestSetFlagHelper$")
	cmd.Env = append(os.Environ(), "V8GO_SET_FLAGS_HELPER=1")
	if output, err := cmd.CombinedOutput(); err != nil {
		t.Fatalf("SetFlags helper failed: %v\n%s", err, output)
	}
}

func TestSetFlagHelper(t *testing.T) {
	if os.Getenv("V8GO_SET_FLAGS_HELPER") != "1" {
		return
	}

	v8.SetFlags("--use_strict")
	ctx := v8.NewContext()
	defer ctx.Isolate().Dispose()
	defer ctx.Close()
	if _, err := ctx.RunScript("a = 1", "use_strict.js"); err == nil {
		t.Error("expected error but got <nil>")
	}

	panicked := false
	func() {
		defer func() {
			panicked = recover() != nil
		}()
		v8.SetFlags("--nouse_strict")
	}()
	if !panicked {
		t.Error("expected SetFlags to panic after V8 initialization")
	}
}
