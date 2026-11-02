package stdoutguard

import (
	"strings"
	"testing"
)

func TestUC1Mode(t *testing.T) {
	cases := map[string]bool{
		"-mode uc1-customer": true, "-mode=uc1-sensor": true, "--mode uc1-deploy": true,
		"-mode hub": false, "-mode local": false, "": false, "-url x -mode uc1-customer -probe-rate 5": true,
		"-mode": false, "-mode uc1-": false,
		// The UC2 player sidecar speaks the same protocol; the hub and the
		// deploy keep their stdout as it was.
		"-mode uc2-player": true, "-mode=uc2-player": true, "-mode uc2-hub": false, "-mode uc2-deploy": false,
	}
	for line, want := range cases {
		if got := uc1Mode(strings.Fields(line)); got != want {
			t.Errorf("%q: got %v, want %v", line, got, want)
		}
	}
}
