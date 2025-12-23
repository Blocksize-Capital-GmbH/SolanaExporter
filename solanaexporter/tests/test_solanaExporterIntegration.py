import os
import unittest
from unittest.mock import MagicMock, patch

from solanaexporter.solanaExporter import SolanaExporter


class TestSolanaExporterIntegration(unittest.TestCase):
    def setUp(self):
        # Define environment variables as a dictionary
        self.env = {
            "SOLANA_RPC_URL": "https://api.testnet.solana.com",
            "SOLANA_PUBLIC_RPC_URL": "https://api.testnet.solana.com",
            "EXPORTER_PORT": "7896",
            "POLL_INTERVAL": "10",
            "VOTE_PUBKEY": "6jJK69aeuLbVnM6nUKnmMMwyQG2rNjKNFrfM459kfAdL",
            "VALIDATOR_PUBKEY": "4EKxPYXmBha7ADnZphFFC13RaKNYLZCiQPKuSV8YWRZc",
            "LABEL": "Integration_Test_Label",
            "VERSION": "1.0.0",
            "DOUBLE_ZERO_FEES_ADDRESS": "11111111111111111111111111111111",
        }

    @patch.dict("os.environ", {}, clear=True)
    @patch("requests.post")
    def test_collect_metrics(self, mock_post):
        """Integration-like test for collect_metrics (mocked RPC)."""
        os.environ.update(self.env)

        resp_identity = MagicMock()
        resp_identity.status_code = 200
        resp_identity.json.return_value = [{"result": {"identity": self.env["VALIDATOR_PUBKEY"]}}]

        resp_batch = MagicMock()
        resp_batch.status_code = 200
        resp_batch.json.return_value = [
            {"result": 12345},  # getSlot
            {"result": {"value": 100_000_000_000}},  # getBalance
            {"result": {"value": 50_000_000_000}},  # getBalance (double_zero)
            {"result": {"current": [], "delinquent": []}},  # getVoteAccounts
            {"result": {"absoluteSlot": 12395, "epoch": 713}},  # getEpochInfo
            {"result": {self.env["VALIDATOR_PUBKEY"]: [1, 2, 3]}},  # getLeaderSchedule
            {"result": {"value": {"byIdentity": {self.env["VALIDATOR_PUBKEY"]: [1, 2]}}}},  # getBlockProduction
            {"result": "ok"},  # getHealth
        ]
        mock_post.side_effect = [resp_identity, resp_batch]

        exporter = SolanaExporter(config_source="fromEnv")
        exporter.collect_metrics()

        self.assertEqual(exporter.slot_number._value.get(), 12345)
        self.assertEqual(exporter.balance._value.get(), 100)
        self.assertEqual(exporter.double_zero_balance._value.get(), 50)
        self.assertIn(exporter.health_status._value.get(), {0, 1})
        self.assertEqual(exporter.epoch._value.get(), 713)

    @patch.dict("os.environ", {}, clear=True)
    @patch("requests.post")
    def test_get_stake_accounts(self, mock_post):
        """Integration-like test for _get_stake_accounts (mocked RPC)."""
        os.environ.update(self.env)

        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {
            "result": [
                {
                    "pubkey": "FpLrg2hkUnFhh9bBpFDtRJTt8VeDbqxq7SubE6kL2HX6",
                    "account": {"lamports": 1_666_666_000_000},
                }
            ]
        }

        exporter = SolanaExporter(config_source="fromEnv")
        stake_accounts = exporter._get_stake_accounts()

        self.assertIsInstance(stake_accounts, list, "Stake accounts should be a list")
        self.assertEqual(len(stake_accounts), 1)
        self.assertEqual(stake_accounts[0].result[0]["account"]["lamports"], 1_666_666_000_000)

    @patch.dict("os.environ", {}, clear=True)
    @patch("requests.post")
    def test_double_zero_fees_balance_mainnet(self, mock_post):
        """Integration-like test for double_zero_fees_address (mocked RPC)."""
        mainnet_env = {
            "SOLANA_RPC_URL": "https://api.mainnet-beta.solana.com",
            "SOLANA_PUBLIC_RPC_URL": "https://api.mainnet-beta.solana.com",
            "EXPORTER_PORT": "7896",
            "POLL_INTERVAL": "120",
            "VOTE_PUBKEY": "HMk1qny4fvMnajErxjXG5kT89JKV4cx1PKa9zhQBF9ib",
            "VALIDATOR_PUBKEY": "BH6aHw9y4Ejes5KdPYA3ezwERCvJd2zMzGLKze45kfy3",
            "LABEL": "Integration_Test_Mainnet",
            "VERSION": "v0.712.30006",
            "DOUBLE_ZERO_FEES_ADDRESS": "4wm9PFxxRox3vgntwVdwbqvkRDjyjaqEdSiohosEJSj5",
        }
        os.environ.update(mainnet_env)

        resp_identity = MagicMock()
        resp_identity.status_code = 200
        resp_identity.json.return_value = [{"result": {"identity": mainnet_env["VALIDATOR_PUBKEY"]}}]

        resp_batch = MagicMock()
        resp_batch.status_code = 200
        resp_batch.json.return_value = [
            {"result": 12345},  # getSlot
            {"result": {"value": 100_000_000_000}},  # getBalance (validator)
            {"result": {"value": 4_890_000_000}},  # getBalance (double_zero_fees_address)
            {"result": {"current": [], "delinquent": []}},  # getVoteAccounts
            {"result": {"absoluteSlot": 12395, "epoch": 713}},  # getEpochInfo
            {"result": {mainnet_env["VALIDATOR_PUBKEY"]: [1, 2, 3]}},  # getLeaderSchedule
            {"result": {"value": {"byIdentity": {mainnet_env["VALIDATOR_PUBKEY"]: [1, 2]}}}},  # getBlockProduction
            {"result": "ok"},  # getHealth
        ]
        mock_post.side_effect = [resp_identity, resp_batch]

        exporter = SolanaExporter(config_source="fromEnv")
        exporter.collect_metrics()

        double_zero_balance = exporter.double_zero_balance._value.get()
        self.assertEqual(double_zero_balance, 4.89)


if __name__ == "__main__":
    unittest.main()
