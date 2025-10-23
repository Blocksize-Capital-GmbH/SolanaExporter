<div align="center">

## SolanaExporter -- _Prometheus Metrics for Validators_

<img src="assets/solana_logo.png" alt="Solana" height="50"/> &nbsp;&nbsp;&nbsp; [<img src="assets/blocksize_logo_white.png" alt="Blocksize" height="50"/>](https://blocksize.info)

[![CI Pipeline](https://github.com/Blocksize-Capital-GmbH/SolanaExporter/actions/workflows/ci.yml/badge.svg)](https://github.com/Blocksize-Capital-GmbH/SolanaExporter/actions)
[![License: MIT](assets/license-badge.svg)](LICENSE)
[![Python 3.9+](assets/python-badge.svg)](https://www.python.org/downloads/)

---

</div>

## Overview

**SolanaExporter** is a Prometheus metrics exporter designed for Solana validator operators. It provides comprehensive monitoring capabilities through RPC queries, enabling operators to track validator health, performance, stake information, and network participation in real-time.

### Key Features

-   📊 **Comprehensive Metrics**: Slot height, sync status, stake delegation, vote distance, block production
-   🏥 **Health Monitoring**: Real-time validator health status and sync lag tracking
-   💰 **Stake Tracking**: Monitor total delegated stake, pending stake, and delinquent stake
-   🎯 **Block Production**: Track leader slots, block production success rate, and missed slots
-   🔄 **Automatic Polling**: Configurable polling intervals for optimal monitoring
-   🐳 **Docker Ready**: Container support with health checks and proper error handling
-   📈 **Prometheus Compatible**: Standard Prometheus exposition format

## Quick Start

### Prerequisites

-   Python 3.9+ or Docker
-   Access to a Solana RPC endpoint
-   Validator public key and vote account public key

### Installation

#### Option 1: Docker (Recommended)

1. **Create environment file** (`.env`):

```bash
SOLANA_RPC_URL=http://localhost:8899
SOLANA_PUBLIC_RPC_URL=https://api.mainnet-beta.solana.com
EXPORTER_PORT=7896
POLL_INTERVAL=30
VOTE_PUBKEY=YourVotePubkeyHere
VALIDATOR_PUBKEY=YourValidatorPubkeyHere
STAKE_ACCOUNT_PUBKEY=YourStakeAccountPubkeyHere
VERSION=1.18.0
LABEL=mainnet-validator
```

2. **Build and run**:

```bash
# Build image
./buildSolanaExporterDockerImage.sh

# Run container
docker run --network host \
  -v $(pwd)/.env:/exporter.env \
  --env EXPORTER_ENV=/exporter.env \
  --name solana_exporter \
  --restart always \
  -d bsc_solana_exporter
```

#### Option 2: From Source

```bash
# Install dependencies
sudo add-apt-repository ppa:deadsnakes/ppa
sudo apt-get update
sudo apt-get install python3.12 python3.12-venv
curl -sSL https://install.python-poetry.org | python3.12 -

# Setup environment
python3.12 -m venv venv
source venv/bin/activate
poetry install

# Run exporter
EXPORTER_ENV=/path/to/.env python solanaexporter/solanaExporter.py
```

## Configuration

### Required Configuration

| Variable                | Description                   | Example                               |
| ----------------------- | ----------------------------- | ------------------------------------- |
| `SOLANA_RPC_URL`        | Your validator's RPC endpoint | `http://localhost:8899`               |
| `SOLANA_PUBLIC_RPC_URL` | Public RPC for reference data | `https://api.mainnet-beta.solana.com` |
| `EXPORTER_PORT`         | Port for Prometheus metrics   | `7896`                                |
| `POLL_INTERVAL`         | Polling interval in seconds   | `30`                                  |
| `VOTE_PUBKEY`           | Vote account public key       | `YourVotePubkey...`                   |
| `VALIDATOR_PUBKEY`      | Validator identity public key | `YourValidatorPubkey...`              |
| `VERSION`               | Solana client version         | `1.18.0`                              |
| `LABEL`                 | Instance label for metrics    | `mainnet-validator`                   |

### Optional Configuration

| Variable                   | Description                             | Example                            |
| -------------------------- | --------------------------------------- | ---------------------------------- |
| `DOUBLE_ZERO_FEES_ADDRESS` | Address to monitor for balance tracking | `11111111111111111111111111111111` |
| `STAKE_ACCOUNT_PUBKEY`     | Specific stake account to monitor       | `YourStakeAccount...`              |

### Finding Your Validator Keys

```bash
# Get validator identity
solana-keygen pubkey /path/to/validator-keypair.json

# Get vote account
solana validators --output json | jq '.currentValidators[] | select(.identityPubkey=="YOUR_IDENTITY") | .voteAccountPubkey'
```

## Metrics

The exporter provides the following Prometheus metrics:

### Core Validator Metrics

-   `solana_slot_number` - Current slot number of your validator
-   `solana_absolute_slot_number` - Absolute slot number of the Solana chain
-   `solana_slot_lag` - Slot lag between your validator and the network
-   `solana_sync_status` - Node sync status (1 = synced, 0 = not synced)
-   `solana_health_status` - Overall health status of the node
-   `solana_epoch` - Current Solana epoch

### Stake Metrics

-   `solana_total_delegated_stake` - Total stake delegated to the validator (in SOL)
-   `solana_pending_stake` - Stake delegated but not yet active (in SOL)
-   `solana_delinquent_stake` - Stake that is delinquent (in SOL)

### Performance Metrics

-   `solana_missed_slots` - Number of slots missed by the validator
-   `solana_leader_status` - Current leader status (1 = leader, 0 = not leader)
-   `solana_vote_distance` - Vote distance from the highest known slot
-   `solana_block_production_success` - Block production success rate
-   `solana_credits_earned` - Vote credits earned

### Account Metrics

-   `solana_account_balance` - Validator account balance (in SOL)
-   `solana_double_zero_balance` - Balance of monitored address (in SOL, if configured)

### Timing Metrics

-   `solana_slot_time` - Time taken to process a slot (seconds)

## Deployment

### Docker Compose Deployment

**docker-compose.yml:**

```yaml
services:
    solana-exporter:
        image: bsc_solana_exporter:latest
        container_name: solana_metrics_exporter
        network_mode: host
        restart: unless-stopped
        environment:
            - EXPORTER_ENV=/app/.env
        volumes:
            - ./.env:/app/.env:ro
        healthcheck:
            test: ["CMD", "curl", "-f", "http://localhost:7896"]
            interval: 30s
            timeout: 10s
            retries: 3
```

**Deploy:**

```bash
docker compose up -d
```

### Prometheus Configuration

Add to your `prometheus.yml`:

```yaml
scrape_configs:
    - job_name: "solana-validator"
      static_configs:
          - targets: ["localhost:7896"]
      scrape_interval: 30s
      scrape_timeout: 10s
```

## Monitoring & Alerting

### Sample Prometheus Alerts

```yaml
groups:
    - name: solana-validator
      rules:
          - alert: SolanaValidatorOutOfSync
            expr: solana_sync_status == 0
            for: 5m
            labels:
                severity: critical
            annotations:
                summary: "Solana validator is out of sync"
                description: "Validator has been out of sync for more than 5 minutes"

          - alert: SolanaHighSlotLag
            expr: solana_slot_lag > 100
            for: 2m
            labels:
                severity: warning
            annotations:
                summary: "High slot lag detected"
                description: "Slot lag is {{ $value }} slots behind"

          - alert: SolanaValidatorUnhealthy
            expr: solana_health_status == 0
            for: 3m
            labels:
                severity: critical
            annotations:
                summary: "Solana validator unhealthy"
                description: "Validator health check failing"

          - alert: SolanaHighMissedSlots
            expr: rate(solana_missed_slots[5m]) > 0.1
            for: 5m
            labels:
                severity: warning
            annotations:
                summary: "High rate of missed slots"
                description: "Validator is missing {{ $value }} slots per second"

          - alert: SolanaLowAccountBalance
            expr: solana_account_balance < 1.0
            for: 1h
            labels:
                severity: warning
            annotations:
                summary: "Low validator account balance"
                description: "Balance is {{ $value }} SOL (< 1 SOL)"
```

### Grafana Dashboard

Create visualizations for:

-   **Sync Status Panel**: Real-time sync status and slot lag
-   **Performance Panel**: Block production rate, missed slots, vote distance
-   **Stake Panel**: Total delegated stake, pending activations, delinquent stake
-   **Health Panel**: Overall validator health and uptime
-   **Network Panel**: Epoch progress, slot time, leader schedule

## Troubleshooting

### Development Environment Setup

```bash
# Create and activate virtual environment
python3.12 -m venv venv
source venv/bin/activate

# Install dependencies with Poetry
poetry install --with dev

# Install pre-commit hooks
poetry run pre-commit install

# Run code quality checks
poetry run pre-commit run --all-files
```

### Common Issues

**Exporter not starting:**

```bash
# Check logs
docker logs solana_exporter

# Verify configuration
cat .env

# Test RPC connectivity
curl -X POST -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"getHealth"}' \
  $SOLANA_RPC_URL
```

**Metrics show zero/stale data:**

```bash
# Verify RPC is responding
curl http://localhost:7896 | grep solana_

# Check polling interval (recommended: 30-60 seconds)
grep POLL_INTERVAL .env

# Ensure validator keys are correct
solana validators | grep $VALIDATOR_PUBKEY
```

**High CPU usage:**

```bash
# Increase poll interval in .env
echo "POLL_INTERVAL=60" >> .env

# Restart exporter
docker restart solana_exporter
```

### Running in Development

```bash
# Activate virtual environment
source venv/bin/activate

# Run with debug logging
export LOG_LEVEL=DEBUG
EXPORTER_ENV=.env poetry run python solanaexporter/solanaExporter.py
```

### Health Check

```bash
# Check exporter health
curl http://localhost:7896

# Verify specific metrics
curl http://localhost:7896 | grep solana_slot_number

# Check if Prometheus is scraping
curl http://prometheus:9090/api/v1/targets
```

## Development

### Running Tests

```bash
# Install dev dependencies
poetry install --with dev

# Run all tests
poetry run pytest

# Run with coverage
poetry run pytest --cov=solanaexporter --cov-report=html

# View coverage report
open coverage/index.html
```

### Code Quality

All code quality checks are managed through pre-commit hooks:

```bash
# Run all quality checks
poetry run pre-commit run --all-files

# Run specific hook
poetry run pre-commit run black --all-files
poetry run pre-commit run pylint --all-files
poetry run pre-commit run mypy --all-files
```

Pre-commit automatically runs checks on staged files before each commit.

## Architecture

```
┌─────────────────┐    ┌──────────────────┐    ┌─────────────────┐
│  Local Solana   │    │   Public Solana  │    │   Prometheus    │
│   RPC Node      │    │     RPC Node     │    │     Server      │
│  (Validator)    │    │   (Reference)    │    │                 │
└─────────────────┘    └──────────────────┘    └─────────────────┘
         │                       │                       ▲
         │                       │                       │
         ▼                       ▼                       │
┌────────────────────────────────────────────────────────┴────┐
│              Solana Metrics Exporter                        │
│  ┌─────────────┐ ┌─────────────┐ ┌─────────────────────┐    │
│  │     RPC     │ │   Metrics   │ │   Health Checker    │    │
│  │   Client    │ │  Collector  │ │                     │    │
│  └─────────────┘ └─────────────┘ └─────────────────────┘    │
│                                                             │
│  Metrics Endpoint: :7896                                    │
└─────────────────────────────────────────────────────────────┘
```

## Contributing

We welcome contributions! Please:

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Make your changes with tests
4. Run code quality checks
5. Commit your changes (`git commit -m 'Add amazing feature'`)
6. Push to the branch (`git push origin feature/amazing-feature`)
7. Open a Pull Request

## Security

-   Exporter requires read-only RPC access
-   No private keys are stored or transmitted
-   All metrics are public validator data
-   Configuration files should be protected (contain public keys only)

For security issues, please contact: security@blocksize-capital.com

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

---

<div align="center">

---

## Made with ❤️ by [Blocksize](https://blocksize.info) to support and secure the Solana ecosystem

Validator Identity: [`HMk1qny4fvMnajErxjXG5kT89JKV4cx1PKa9zhQBF9ib`](https://solscan.io/account/HMk1qny4fvMnajErxjXG5kT89JKV4cx1PKa9zhQBF9ib)

[![Stake on Blocksize](assets/stake-blocksize-badge.svg)](https://blocksize.info/staking/protocols/solana/)
[![Stake on Kiwi](assets/stake-kiwi-badge.svg)](https://staking.kiwi/app/HMk1qny4fvMnajErxjXG5kT89JKV4cx1PKa9zhQBF9ib)

</div>
