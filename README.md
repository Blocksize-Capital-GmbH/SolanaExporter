```
__________.__                 __           .__
\______   \  |   ____   ____ |  | __  _____|__|_______ ____
 |    |  _/  |  /  _ \_/ ___\|  |/ / /  ___/  \___   // __ \
 |    |   \  |_(  <_> )  \___|    <  \___ \|  |/    /\  ___/
 |______  /____/\____/ \___  >__|_ \/____  >__/_____ \\___  >
        \/                 \/     \/     \/         \/    \/
  _________________  .____       _____    _______      _____
 /   _____/\_____  \ |    |     /  _  \   \      \    /  _  \
 \_____  \  /   |   \|    |    /  /_\  \  /   |   \  /  /_\  \
 /        \/    |    \    |___/    |    \/    |    \/    |    \
/_______  /\_______  /_______ \____|__  /\____|__  /\____|__  /
        \/         \/        \/       \/         \/         \/
                                     __
  ____ ___  _________   ____________/  |_  ___________
_/ __ \\  \/  /\____ \ /  _ \_  __ \   __\/ __ \_  __ \
\  ___/ >    < |  |_> >  <_> )  | \/|  | \  ___/|  | \/
 \___  >__/\_ \|   __/ \____/|__|   |__|  \___  >__|
     \/      \/|__|                           \/
```

# Synopsis

The Blocksize Solana Exporter provides easy-to-deploy prometheus metrics for validator operations on Solana or the Pyth obtained via queries to the RPC in regular intervals.

# Configuration

Edit [solanaExporter.env](./solanaExporter.env) or use the following template

```bash
SOLANA_RPC_URL=< solana RPC to monitor >
SOLANA_PUBLIC_RPC_URL=< public RPC for reference >
EXPORTER_PORT=< port for prometheus metrics >
POLL_INTERVAL=< interval for triggering RPC requests in seconds >
VOTE_PUBKEY=< vote public key to monitor >
VALIDATOR_PUBKEY=< validator public key to monitor >
STAKE_ACCOUNT_PUBKEY=< stake account to monitor >
VERSION=< your client version >
LABEL=< label of your solana instance >
```

# Developer Setup

## Prerequisites

Install python3.12 and poetry

```bash
sudo add-apt-repository ppa:deadsnakes/ppa
sudo apt-get update
sudo apt-get install python3.12 python3.12-venv
curl -sSL https://install.python-poetry.org | python3.12 -
```

## Create environment

```bash
python3.12 -m venv venv
source venv/bin/activate
poetry install
```

## Run locally from source

`EXPORTER_ENV=<path_to_env> python solanaexporter/solanaExporter.py`

# Build and run dockerized

To build a docker image named `bsc_solana_exporter`:

Run [./buildSolanaExporterDockerImage.sh](./buildSolanaExporterDockerImage.sh)

To run:

```bash

IMG=bsc_solana_exporter CONF=<path_to_config> docker run --network host \
-v ${CONF}:/exporter.env \
--env EXPORTER_ENV=/exporter.env \
--name ${IMG} \
--restart always \
-itd bsc_solana_exporter
```
