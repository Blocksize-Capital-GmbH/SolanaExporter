#!/bin/bash

curl -X POST https://api.testnet.solana.com \
-H "Content-Type: application/json" \
-d '{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "getProgramAccounts",
  "params": [
    "Stake11111111111111111111111111111111111111",
    {
      "filters": [
        {"dataSize": 200},
        {
          "memcmp": {
            "offset": 124,
            "bytes": "6jJK69aeuLbVnM6nUKnmMMwyQG2rNjKNFrfM459kfAdL"
          }
        }
      ],
      "encoding": "base64"
    }
  ]
}'
