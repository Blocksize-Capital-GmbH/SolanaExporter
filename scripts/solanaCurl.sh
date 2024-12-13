#!/bin/bash

# Load configuration from ../solanaExporter.env
if [[ -f ../solanaExporter.env ]]; then
  source ../solanaExporter.env
else
  echo "Configuration file ../solanaExporter.env not found. Please create it with the following variables:"
  echo "SOLANA_RPC_URL, VOTE_PUBKEY, VALIDATOR_PUBKEY, STAKE_ACCOUNT_PUBKEY"
  exit 1
fi

# Check if required environment variables are set
if [[ -z "$SOLANA_RPC_URL" || -z "$VOTE_PUBKEY" || -z "$VALIDATOR_PUBKEY" || -z "$STAKE_ACCOUNT_PUBKEY" ]]; then
  echo "Error: One or more required environment variables are missing."
  echo "Please ensure SOLANA_RPC_URL, VOTE_PUBKEY, VALIDATOR_PUBKEY, and STAKE_ACCOUNT_PUBKEY are set in ../solanaExporter.env."
  exit 1
fi

# Function to display usage message
usage() {
    echo "Usage: $0 -m METHOD [-p PARAMS] [-h]"
    echo
    echo "Options:"
    echo "  -m METHOD       RPC method to invoke (e.g., getBalance, getSlot, getVoteAccounts)"
    echo "  -p PARAMS       Parameters for the RPC method as a JSON array (e.g., '[\"address\"]')"
    echo "  -h              Show help message and usage"
    echo
    echo "Available Methods and Links to Documentation:"
    echo
    echo "  getBalance                   [https://solana.com/docs/rpc/http/getBalance]"
    echo "    Purpose: Retrieves the balance of a given account (e.g., validator's main account)."
    echo "    Example: $0 -m getBalance -p '[\"$VALIDATOR_PUBKEY\"]'"
    echo
    echo "  getSlot                      [https://solana.com/docs/rpc/http/getSlot]"
    echo "    Purpose: Retrieves the current slot, useful for checking synchronization."
    echo "    Example: $0 -m getSlot"
    echo
    echo "  getVoteAccounts              [https://solana.com/docs/rpc/http/getVoteAccounts]"
    echo "    Purpose: Retrieves all vote accounts, with details about each validator's vote history."
    echo "    Example: $0 -m getVoteAccounts"
    echo
    echo "  getBlockProduction           [https://solana.com/docs/rpc/http/getBlockProduction]"
    echo "    Purpose: Provides block production stats for the validator."
    echo "    Example: $0 -m getBlockProduction -p '[{\"identity\": \"$VALIDATOR_PUBKEY\"}]'"
    echo
    echo "  getInflationReward           [https://solana.com/docs/rpc/http/getInflationReward]"
    echo "    Purpose: Fetches the inflation reward earned by accounts in a given epoch."
    echo "    Example: $0 -m getInflationReward -p '[\"[$STAKE_ACCOUNT_PUBKEY]\", {\"epoch\": 100}]'"
    echo
    echo "  getEpochInfo                 [https://solana.com/docs/rpc/http/getEpochInfo]"
    echo "    Purpose: Provides information about the current epoch, including start and end slot."
    echo "    Example: $0 -m getEpochInfo"
    echo
    echo "  getProgramAccounts           [https://solana.com/docs/rpc/http/getProgramAccounts]"
    echo "    Purpose: Lists all accounts associated with a program, useful for finding all stake accounts."
    echo "    Example: $0 -m getProgramAccounts -p '[\"StakeProgram111111111111111111111111111111111111111\", {\"filters\": [{\"memcmp\": {\"offset\": 12, \"bytes\": \"$VOTE_PUBKEY\"}}]}]'"
    echo
    echo "  getAccountInfo               [https://solana.com/docs/rpc/http/getAccountInfo]"
    echo "    Purpose: Retrieves details about a specific account, like the validator's account."
    echo "    Example: $0 -m getAccountInfo -p '[\"$VALIDATOR_PUBKEY\"]'"
    echo
    exit 1
}

# Parse command-line options
while getopts "m:p:h" opt; do
  case $opt in
    m) METHOD=$OPTARG ;;
    p) PARAMS=$OPTARG ;;
    h) usage ;;
    *) usage ;;
  esac
done

# Ensure METHOD is provided
if [ -z "$METHOD" ]; then
    echo "Error: RPC METHOD is required."
    usage
fi

# Prepare the JSON payload
if [ -n "$PARAMS" ]; then
    REQUEST_DATA="{\"jsonrpc\":\"2.0\", \"id\":1, \"method\":\"$METHOD\", \"params\":$PARAMS}"
else
    REQUEST_DATA="{\"jsonrpc\":\"2.0\", \"id\":1, \"method\":\"$METHOD\", \"params\":[]}"
fi

# Make the RPC call
echo "Calling Solana RPC Method: $METHOD"
echo "Parameters: ${PARAMS:-'[]'}"
echo
RESULT=$(curl -s -X POST -H "Content-Type: application/json" -d "$REQUEST_DATA" "$SOLANA_RPC_URL")

# Display the request and the result
echo "Request JSON:"
echo "$REQUEST_DATA"
echo
echo "Response:"
echo "$RESULT" | jq .

# Exit with the status of the last command executed
exit $?
