package main

import (
	"net"
	"testing"
)

func TestEndpointHost(t *testing.T) {
	host, err := endpointHost("192.0.2.10:9000")
	if err != nil || host != "192.0.2.10" {
		t.Fatalf("host=%q err=%v", host, err)
	}
	host, err = endpointHost("[::1]:9000")
	if err != nil || host != "::1" {
		t.Fatalf("IPv6 host=%q err=%v", host, err)
	}
}

func TestCheckLocalEndpointLoopback(t *testing.T) {
	if err := checkLocalEndpoint("127.0.0.1:9000"); err != nil {
		t.Fatal(err)
	}
}

func TestCheckPortFreeRejectsListener(t *testing.T) {
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer listener.Close()
	if err := checkPortFree(listener.Addr().String()); err == nil {
		t.Fatal("expected occupied port to fail")
	}
}
