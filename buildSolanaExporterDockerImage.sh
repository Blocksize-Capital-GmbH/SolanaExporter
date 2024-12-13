#!/bin/bash

docker build . --file ./SolanaExporterDockerfile --no-cache -t bsc_solana_exporter
