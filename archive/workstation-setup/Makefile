# L2-benchmark environment — convenience targets.
# The cloned frameworks and the zksync binary are NOT committed; setup.sh
# reconstructs them at pinned versions (see STATUS.md).

.PHONY: setup verify clean

setup:        ## Clone/build all components at pinned versions (excludes go-nitro & hub demo)
	./setup.sh

verify:       ## Re-check that the key components build / run
	cd perun/go-perun          && go build ./...
	cd perun/perun-eth-backend && go build ./...
	cd perun/perun-examples/payment-channel && go build ./...
	./zksync/anvil-zksync --version

clean:        ## Remove the reconstructable components (keeps STATUS.md, setup.sh, hub demo)
	rm -rf perun/go-perun perun/perun-eth-backend perun/perun-examples nitro-testnode zksync
