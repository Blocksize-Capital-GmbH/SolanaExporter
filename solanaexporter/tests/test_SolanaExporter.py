import unittest
from unittest.mock import patch

from solanaexporter.solanaExporter import SolanaExporter


class TestSolanaExporter(unittest.TestCase):
    def setUp(self):
        self.env = {
            "SOLANA_RPC_URL": "http://localhost:8899",
            "SOLANA_PUBLIC_RPC_URL": "https://api.testnet.solana.com",
            "EXPORTER_PORT": "7896",
            "POLL_INTERVAL": "10",
            "VOTE_PUBKEY": "6jJK69aeuLbVnM6nUKnmMMwyQG2rNjKNFrfM459kfAdL",
            "VALIDATOR_PUBKEY": "4EKxPYXmBha7ADnZphFFC13RaKNYLZCiQPKuSV8YWRZc",
            "STAKE_ACCOUNT_PUBKEY": "J1XibEzMT4pAhu6yBFs2EdsK8nSrVcCao3Ut4eYytzmw",
            "LABEL": "Blocksize_Testnet_Main",
            "VERSION": "0.708.20306",
            "DOUBLE_ZERO_FEES_ADDRESS": "11111111111111111111111111111111",
        }

    @patch("os.environ", new_callable=lambda: {})
    @patch("requests.post")
    def test_collect_metrics(self, mock_post, mock_env):
        """Test metrics collection."""
        mock_env.update(self.env)
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = [
            {"result": 12345},  # getSlot
            {"result": {"value": 100_000_000_000}},  # getBalance
            {"result": {"value": 50_000_000_000}},  # getBalance (double_zero_fees_address)
            {"result": {"current": [], "delinquent": []}},  # getVoteAccounts
            {"result": {"absoluteSlot": 12395, "epoch": 713}},  # getEpochInfo
            {"result": {"6jJK69aeuLbVnM6nUKnmMMwyQG2rNjKNFrfM459kfAdL": [1, 2, 3]}},  # getLeaderSchedule
            {
                "result": {"value": {"byIdentity": {"4EKxPYXmBha7ADnZphFFC13RaKNYLZCiQPKuSV8YWRZc": [1, 2]}}}
            },  # getBlockProduction
            {"result": "ok"},  # getHealth
        ]

        exporter = SolanaExporter(config_source="fromEnv")
        exporter.collect_metrics()

        self.assertEqual(exporter.slot_number._value.get(), 12345)
        self.assertEqual(exporter.balance._value.get(), 100)
        self.assertEqual(exporter.double_zero_balance._value.get(), 50)
        # Check that build_info contains the expected version and label strings
        build_info_labels = exporter.build_info._value
        self.assertEqual(build_info_labels.get("version"), "0.708.20306")
        self.assertEqual(build_info_labels.get("label"), "Blocksize_Testnet_Main")

    @patch("requests.post")
    @patch.dict(
        "os.environ",
        {
            "SOLANA_PUBLIC_RPC_URL": "https://api.testnet.solana.com",
            "SOLANA_RPC_URL": "http://localhost:8899",
            "EXPORTER_PORT": "7896",
            "POLL_INTERVAL": "10",
            "VOTE_PUBKEY": "6jJK69aeuLbVnM6nUKnmMMwyQG2rNjKNFrfM459kfAdL",
            "VALIDATOR_PUBKEY": "4EKxPYXmBha7ADnZphFFC13RaKNYLZCiQPKuSV8YWRZc",
            "STAKE_ACCOUNT_PUBKEY": "J1XibEzMT4pAhu6yBFs2EdsK8nSrVcCao3Ut4eYytzmw",
            "VERSION": "0.708.20306",
            "LABEL": "Blocksize_Testnet_Main",
            "DOUBLE_ZERO_FEES_ADDRESS": "11111111111111111111111111111111",
        },
    )
    def test_get_stake_accounts(self, mock_post):
        """Test fetching stake accounts."""
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

        self.assertEqual(len(stake_accounts), 1)
        self.assertEqual(stake_accounts[0].result[0]["account"]["lamports"], 1_666_666_000_000)

    @patch("os.environ", new_callable=lambda: {})
    @patch("requests.post")
    def test_update_stake_metrics(self, mock_post, mock_env):
        """Test updating stake metrics."""
        mock_env.update(self.env)
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = [
            {
                "result": [
                    {
                        "pubkey": "FpLrg2hkUnFhh9bBpFDtRJTt8VeDbqxq7SubE6kL2HX6",
                        "account": {"lamports": 1_666_666_000_000},
                    }
                ]
            }
        ]

        exporter = SolanaExporter(config_source="fromEnv")
        vote_accounts = {
            "current": [{"votePubkey": self.env["VOTE_PUBKEY"], "activatedStake": 500_000_000_000}],
            "delinquent": [{"votePubkey": self.env["VOTE_PUBKEY"], "activatedStake": 200_000_000_000}],
        }
        exporter.stake_accounts = exporter._get_stake_accounts()
        exporter._update_stake_metrics(vote_accounts)

        self.assertEqual(exporter.total_delegated_stake._value.get(), 500)
        self.assertEqual(exporter.delinquent_stake._value.get(), 200)
        self.assertAlmostEqual(exporter.pending_stake._value.get(), 1_666.666 - 500)

    @patch("os.environ", new_callable=lambda: {})
    @patch("requests.post")
    def test_collect_metrics_without_double_zero(self, mock_post, mock_env):
        """Test metrics collection without double_zero_fees_address configured."""
        # Setup environment without DOUBLE_ZERO_FEES_ADDRESS
        env_without_double_zero = {k: v for k, v in self.env.items() if k != "DOUBLE_ZERO_FEES_ADDRESS"}
        mock_env.update(env_without_double_zero)
        mock_post.return_value.status_code = 200
        # Without double_zero, we have 7 requests instead of 8
        mock_post.return_value.json.return_value = [
            {"result": 12345},  # getSlot
            {"result": {"value": 100_000_000_000}},  # getBalance
            {"result": {"current": [], "delinquent": []}},  # getVoteAccounts
            {"result": {"absoluteSlot": 12395, "epoch": 713}},  # getEpochInfo
            {"result": {"6jJK69aeuLbVnM6nUKnmMMwyQG2rNjKNFrfM459kfAdL": [1, 2, 3]}},  # getLeaderSchedule
            {
                "result": {"value": {"byIdentity": {"4EKxPYXmBha7ADnZphFFC13RaKNYLZCiQPKuSV8YWRZc": [1, 2]}}}
            },  # getBlockProduction
            {"result": "ok"},  # getHealth
        ]

        exporter = SolanaExporter(config_source="fromEnv")
        exporter.collect_metrics()

        self.assertEqual(exporter.slot_number._value.get(), 12345)
        self.assertEqual(exporter.balance._value.get(), 100)
        # double_zero_balance should not be set (or remain at initial value)


if __name__ == "__main__":
    unittest.main()
