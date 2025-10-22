# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- New `double_zero_balance` metric to track balance of double zero fees address
- Optional `DOUBLE_ZERO_FEES_ADDRESS` configuration parameter
- Support for conditional RPC requests based on optional configuration
- Configuration keys are now defined per-exporter instance (moved from RPCexporter base)
- Comprehensive test coverage for optional metric scenarios

### Changed
- Updated to use RPCexporter's new configurable keys API
- `SolanaExporter` now defines its own `REQUIRED_CONFIG_KEYS`, `OPTIONAL_CONFIG_KEYS`, and `ALL_CONFIG_KEYS`
- RPC request indices dynamically adjusted based on presence of optional metrics
- Updated dependency to RPCexporter `feature/configurable-keys` branch

### Dependencies
- RPCexporter updated to support configurable configuration keys per exporter instance
- RPCexporter now supports optional configuration parameters

### Configuration
New optional parameter:
```bash
# Optional: Address to monitor for double zero fees balance
# If not set, the double_zero_balance metric will not be exposed
DOUBLE_ZERO_FEES_ADDRESS=YourSolanaAddressHere
```

### Migration Notes
No breaking changes for existing deployments. The `DOUBLE_ZERO_FEES_ADDRESS` parameter is optional and can be omitted.

If you want to use the new `double_zero_balance` metric, simply add the environment variable to your configuration.

### Technical Details
The implementation uses conditional RPC requests and dynamic response parsing to handle optional metrics:
- If `DOUBLE_ZERO_FEES_ADDRESS` is configured, an additional `getBalance` RPC call is made
- Response parsing dynamically adjusts indices based on whether the optional call was made
- Tests cover both scenarios (with and without the optional configuration)

## [Previous Versions]

_(Add previous version history here if available)_

