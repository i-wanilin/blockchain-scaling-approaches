// Package stdoutguard keeps the UC1 modes' stdout clean.
//
// A dependency prints to stdout while it initialises: perun-eth-backend's
// channel package imports a test helper (polycry.pt/poly-go/test) whose init
// writes "pkg/test: using rootSeed N". The UC1 customer sidecar speaks JSON
// lines on stdout, so that line would corrupt the protocol.
//
// Go initialises packages in import-path order among those whose imports are
// ready, so this package (no imports but os and syscall; its path sorts before
// "polycry.pt/…") runs before that helper. In a uc1-* mode (and uc2-player) it moves the real
// stdout to a new descriptor (Protocol) and points descriptor 1 at stderr, so
// any stray print lands in the log. In every other mode it does nothing.
package stdoutguard

import "os"

// Protocol is where a UC1 mode writes its machine-readable output: the
// process's real stdout.
var Protocol = os.Stdout

// Active reports whether descriptor 1 was redirected to stderr.
var Active bool

func init() {
	if !uc1Mode(os.Args[1:]) {
		return
	}
	if f, ok := redirect(); ok {
		Protocol, Active = f, true
	}
}

// uc1Mode reports whether the command line selects a mode whose stdout is a
// protocol: a uc1-* mode, or uc2-player (the UC2 sidecar, same protocol).
func uc1Mode(args []string) bool {
	for i, a := range args {
		name, value, hasValue := a, "", false
		for _, prefix := range []string{"--", "-"} {
			if len(name) > len(prefix) && name[:len(prefix)] == prefix {
				name = name[len(prefix):]
				break
			}
		}
		for j := 0; j < len(name); j++ {
			if name[j] == '=' {
				name, value, hasValue = name[:j], name[j+1:], true
				break
			}
		}
		if name != "mode" {
			continue
		}
		if !hasValue && i+1 < len(args) {
			value = args[i+1]
		}
		return (len(value) > 4 && value[:4] == "uc1-") || value == "uc2-player"
	}
	return false
}
