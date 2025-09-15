import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from solanaexporter.utils import STAKE_PROGRAM_ID, get_transaction_count, main


class TestRoutines(unittest.TestCase):
    @patch("solanaexporter.utils.AsyncClient")
    async def test_main(self, mock_client_class):
        mock_client = AsyncMock()
        mock_client.is_connected.return_value = True
        mock_client.get_program_accounts.return_value = {
            "result": [
                {"pubkey": "mockPubkey1", "account": "mockAccount1"},
                {"pubkey": "mockPubkey2", "account": "mockAccount2"},
            ]
        }
        mock_client_class.return_value = mock_client

        await main()

        mock_client.is_connected.assert_called_once()
        mock_client.get_program_accounts.assert_called_once_with(
            STAKE_PROGRAM_ID,
            encoding="base64",
            data_size=200,
            memcmp_opts=[{"offset": 124, "bytes": "CAf8jfgqhia5VNrEF4A7Y9VLD3numMq9DVSceq7cPhNY"}],
        )
        mock_client.close.assert_called_once()

    @patch("solanaexporter.utils.requests.post")
    def test_get_transaction_count(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.side_effect = [
            {"result": [{"signature": "sig1"}, {"signature": "sig2"}]},
            {"result": []},
        ]
        mock_post.return_value = mock_response

        public_key = "mockPublicKey"
        count = get_transaction_count(public_key)

        self.assertEqual(count, 2)

        mock_post.assert_any_call(
            "https://api2.pythnet.pyth.network",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getSignaturesForAddress",
                "params": [
                    public_key,
                    {"limit": 1000, "before": None},
                ],
            },
            headers={"Content-Type": "application/json"},
            timeout=15,
        )
        mock_post.assert_any_call(
            "https://api2.pythnet.pyth.network",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getSignaturesForAddress",
                "params": [
                    public_key,
                    {"limit": 1000, "before": None},
                ],
            },
            headers={"Content-Type": "application/json"},
            timeout=15,
        )


if __name__ == "__main__":
    unittest.main()
