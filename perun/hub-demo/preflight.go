package main

import (
	"context"
	"fmt"
	"math/big"
	"net"
	"net/url"
	"strings"
	"time"

	"github.com/ethereum/go-ethereum/ethclient"
)

func endpointHost(endpoint string) (string, error) {
	host, _, err := net.SplitHostPort(endpoint)
	if err != nil {
		return "", err
	}
	return strings.Trim(host, "[]"), nil
}

func localIPSet() (map[string]bool, error) {
	addrs, err := net.InterfaceAddrs()
	if err != nil {
		return nil, err
	}
	ips := make(map[string]bool)
	for _, addr := range addrs {
		var ip net.IP
		switch value := addr.(type) {
		case *net.IPNet:
			ip = value.IP
		case *net.IPAddr:
			ip = value.IP
		}
		if ip != nil {
			ips[ip.String()] = true
		}
	}
	return ips, nil
}

func checkLocalEndpoint(endpoint string) error {
	host, err := endpointHost(endpoint)
	if err != nil {
		return fmt.Errorf("parse endpoint %q: %w", endpoint, err)
	}
	ip := net.ParseIP(host)
	if ip == nil {
		resolved, err := net.LookupIP(host)
		if err != nil {
			return fmt.Errorf("resolve endpoint host %q: %w", host, err)
		}
		if len(resolved) == 0 {
			return fmt.Errorf("endpoint host %q resolved to no addresses", host)
		}
		local, err := localIPSet()
		if err != nil {
			return err
		}
		for _, resolvedIP := range resolved {
			if local[resolvedIP.String()] {
				return nil
			}
		}
		return fmt.Errorf("endpoint host %q does not resolve to this machine", host)
	}
	local, err := localIPSet()
	if err != nil {
		return err
	}
	if !local[ip.String()] {
		return fmt.Errorf("endpoint %s is not assigned to this machine", endpoint)
	}
	return nil
}

func checkPortFree(bind string) error {
	listener, err := net.Listen("tcp", bind)
	if err != nil {
		return fmt.Errorf("listen test %s: %w", bind, err)
	}
	return listener.Close()
}

func checkChain(cfg clusterConfig) error {
	ctx, cancel := context.WithTimeout(context.Background(), 8*time.Second)
	defer cancel()
	chain, err := ethclient.DialContext(ctx, cfg.ChainURL)
	if err != nil {
		return fmt.Errorf("connect chain %s: %w", cfg.ChainURL, err)
	}
	defer chain.Close()
	id, err := chain.ChainID(ctx)
	if err != nil {
		return fmt.Errorf("read chain ID: %w", err)
	}
	if id.Cmp(new(big.Int).SetUint64(cfg.ChainID)) != 0 {
		return fmt.Errorf("chain ID is %s, config requires %d", id, cfg.ChainID)
	}
	return nil
}

func runPreflight(cfg clusterConfig, node string) error {
	if err := checkChain(cfg); err != nil {
		return err
	}
	if node == "" || strings.EqualFold(node, "hub") || strings.EqualFold(node, cfg.Hub.Name) {
		if err := checkLocalEndpoint(cfg.Hub.Endpoint); err != nil {
			return fmt.Errorf("hub endpoint: %w", err)
		}
		if err := checkPortFree(cfg.Hub.Bind); err != nil {
			return err
		}
		controlURL, err := url.Parse("http://" + cfg.Hub.Control)
		if err != nil {
			return fmt.Errorf("parse hub control bind: %w", err)
		}
		if err := checkPortFree(controlURL.Host); err != nil {
			return err
		}
		fmt.Printf("preflight ok: role=hub endpoint=%s chain=%s chain_id=%d\n", cfg.Hub.Endpoint, cfg.ChainURL, cfg.ChainID)
		return nil
	}
	clientCfg, ok := clusterClient(cfg, node)
	if !ok {
		return fmt.Errorf("unknown node %q", node)
	}
	if err := checkLocalEndpoint(clientCfg.Endpoint); err != nil {
		return fmt.Errorf("%s endpoint: %w", node, err)
	}
	if err := checkPortFree(clientCfg.Bind); err != nil {
		return err
	}
	var man manifest
	if err := httpJSON("GET", strings.TrimRight(clientCfg.Control, "/")+"/v1/manifest", nil, &man); err != nil {
		return fmt.Errorf("hub control: %w", err)
	}
	if man.ChainID != cfg.ChainID || man.Hub.Endpoint != cfg.Hub.Endpoint {
		return fmt.Errorf("hub manifest does not match cluster config")
	}
	fmt.Printf("preflight ok: role=%s endpoint=%s hub=%s chain_id=%d run=%s\n",
		node, clientCfg.Endpoint, man.Hub.Endpoint, man.ChainID, man.RunID)
	return nil
}
