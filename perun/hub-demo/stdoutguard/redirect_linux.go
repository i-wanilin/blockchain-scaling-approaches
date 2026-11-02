//go:build linux

package stdoutguard

import (
	"os"
	"syscall"
)

// redirect duplicates descriptor 1 for the protocol and points 1 at stderr.
func redirect() (*os.File, bool) {
	fd, err := syscall.Dup(1)
	if err != nil {
		return nil, false
	}
	syscall.CloseOnExec(fd)
	if err := syscall.Dup3(2, 1, 0); err != nil {
		_ = syscall.Close(fd)
		return nil, false
	}
	return os.NewFile(uintptr(fd), "/dev/stdout"), true
}
