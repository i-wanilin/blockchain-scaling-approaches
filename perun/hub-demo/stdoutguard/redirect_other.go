//go:build !linux

package stdoutguard

import "os"

// redirect is a no-op off Linux (the cluster runs Linux); stray prints then
// reach stdout as before.
func redirect() (*os.File, bool) { return nil, false }
