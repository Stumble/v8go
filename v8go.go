// Copyright 2019 Roger Chapman and the v8go contributors. All rights reserved.
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

/*
Package v8go provides an API to execute JavaScript.
*/
package v8go

// #include "v8go.h"
// #include <stdlib.h>
import "C"
import (
	"strings"
	"sync"
	"unsafe"
)

// Version returns the version of the V8 Engine with the -v8go suffix
func Version() string {
	return C.GoString(C.Version())
}

// SetFlags sets flags for V8. For possible flags: https://github.com/v8/v8/blob/master/src/flags/flag-definitions.h
// Flags are expected to be prefixed with `--`, for example: `--harmony`.
// Flags can be reverted using the `--no` prefix equivalent, for example: `--use_strict` vs `--nouse_strict`.
// Flags affect every Isolate in the process and must be set before the first
// Isolate or Context is created. SetFlags panics after V8 initialization.
func SetFlags(flags ...string) {
	v8InitMutex.Lock()
	defer v8InitMutex.Unlock()
	if v8Initialized {
		panic("v8go: SetFlags must be called before V8 initialization")
	}

	cflags := C.CString(strings.Join(flags, " "))
	C.SetFlags(cflags)
	C.free(unsafe.Pointer(cflags))
}

func initializeIfNecessary() {
	v8InitMutex.Lock()
	defer v8InitMutex.Unlock()
	v8once.Do(func() {
		C.Init()
		v8Initialized = true
	})
}

var (
	v8once        sync.Once
	v8InitMutex   sync.Mutex
	v8Initialized bool
)
