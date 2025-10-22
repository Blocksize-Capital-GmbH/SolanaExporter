# Advanced CI/CD Pipeline Documentation

This document describes the advanced GitHub Actions CI/CD pipeline for SolanaExporter, modeled after the SupraExporter's comprehensive setup.

## Overview

The CI pipeline has been completely redesigned to provide:

1. **Branch-specific workflows** - Different CI checks for main vs feature branches
2. **Reusable workflows** - Modular, maintainable workflow components
3. **Cached environments** - Significant speed improvements through artifact caching
4. **Job inputs/outputs** - Clean data flow between workflow steps
5. **Security scanning** - Trivy vulnerability scanning on main branch
6. **Smart Docker tagging** - Automatic tagging based on branch and release

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    main.yml (Entry Point)                │
│        Routes to appropriate workflow based on branch    │
└─────────────────────┬───────────────────────────────────┘
                      │
          ┌───────────┴───────────┐
          │                       │
          ▼                       ▼
┌─────────────────────┐  ┌──────────────────────┐
│  main-branch-ci.yml │  │ feature-branch-ci.yml │
│  (Comprehensive)    │  │  (Fast Feedback)      │
└──────────┬──────────┘  └──────────┬───────────┘
           │                        │
           └────────┬───────────────┘
                    │
                    ▼
       ┌────────────────────────┐
       │  setup-environment.yml │
       │  (Creates artifacts)   │
       └────────────┬───────────┘
                    │
                    ▼
       ┌────────────────────────┐
       │   Individual Job       │
       │   Workflows:           │
       │   - tests.yml          │
       │   - lint.yml           │
       │   - format.yml         │
       │   - type-check.yml     │
       │   - docstring-check.yml│
       │   - docker.yml         │
       └────────────────────────┘
```

## Workflow Files

### Entry Point

#### `main.yml`
- **Purpose**: Main entry point that routes to appropriate workflows
- **Triggers**: Push to main/feature branches, PRs to main, version tags
- **Logic**: 
  - Main branch pushes → `main-branch-ci.yml` (comprehensive checks)
  - Feature branches/PRs → `feature-branch-ci.yml` (fast feedback)

### Branch-Specific Workflows

#### `main-branch-ci.yml`
- **Purpose**: Comprehensive checks for main branch
- **Features**:
  - Runs all pre-commit hooks via `pre-commit-all.yml`
  - Full test coverage required
  - Security scanning with Trivy
  - Docker image build and push with `latest` tag
- **Philosophy**: Ensure highest quality on main branch

#### `feature-branch-ci.yml`
- **Purpose**: Fast feedback for feature branches
- **Features**:
  - Individual checks (format, lint, type-check, docstring)
  - Tests without coverage (faster execution)
  - Docker image build with branch-specific tag
- **Philosophy**: Quick iteration and developer productivity

### Environment Setup

#### `setup-environment.yml`
- **Purpose**: Creates a unified artifacts package with all dependencies
- **What it does**:
  1. Sets up Python environment
  2. Checks out code to `artifacts/source`
  3. Configures Poetry with custom directories
  4. Caches Poetry directories (speeds up subsequent runs)
  5. Installs all project dependencies
  6. Creates `python-env.sh` configuration script
  7. Uploads everything as a single artifact
- **Key Innovation**: Environment is set up once and reused across all jobs
- **Output**: `solanaexporter-artifacts-{run_id}` artifact

### Composite Action

#### `.github/actions/restore-environment/action.yml`
- **Purpose**: Restore the environment from artifacts in each job
- **What it does**:
  1. Downloads artifacts (done by calling workflow)
  2. Sets up Python
  3. Installs Poetry
  4. Restores executable permissions
  5. Sources environment configuration
  6. Exports environment variables for job
- **Usage**: Every job workflow uses this action

### Individual Job Workflows

All job workflows follow the same pattern:

1. **Download artifacts** - Get the cached environment
2. **Copy .github directory** - Ensure composite action is available
3. **Restore environment** - Use composite action
4. **Run checks** - Execute the specific check

#### `tests.yml`
- **Input**: `skip-coverage` (true/false)
- **Main branch**: Full coverage required (70% threshold)
- **Feature branch**: Skip coverage for speed
- **Outputs**: HTML coverage report (main branch only)

#### `lint.yml`
- Runs flake8 and codespell via pre-commit

#### `format.yml`
- Checks autopep8, prettier, black, isort formatting

#### `type-check.yml`
- Runs mypy static type checking
- Set to `continue-on-error: true` (non-blocking)

#### `docstring-check.yml`
- Runs pydocstyle for documentation strings
- Set to `continue-on-error: true` (non-blocking)

#### `pre-commit-all.yml`
- Runs ALL pre-commit hooks at once
- Only used on main branch for comprehensive validation

#### `docker.yml`
- **Input**: `build-type` (main/branch), `branch-name`
- **Main branch**: Tags as `latest` + SHA
- **Feature branch**: Tags as `develop-{branch-name}` + SHA
- **Features**:
  - Docker Buildx for efficient building
  - GitHub Container Registry (ghcr.io)
  - Docker layer caching
  - Automatic package visibility setting

## Key Features

### 1. Reusable Workflows

All workflows use `workflow_call` trigger, making them composable:

```yaml
on:
    workflow_call:
        inputs:
            python-version:
                required: false
                type: string
                default: "3.12"
```

### 2. Cached Environment

The setup-environment workflow creates a complete environment once:

```
artifacts/
├── source/          # Complete source code
├── venv/            # Poetry virtual environment
├── poetry-cache/    # Poetry cache (for dependency downloads)
├── poetry-data/     # Poetry data
├── github-actions/  # Copy of .github for composite action
└── python-env.sh    # Environment configuration script
```

**Time Savings**: Instead of running `poetry install` in every job (2-3 minutes each), 
we do it once and reuse it across all jobs (15-30 seconds per job).

### 3. Job Inputs and Outputs

Workflows can accept parameters and pass data:

```yaml
inputs:
    skip-coverage:
        description: "Skip coverage checks"
        required: false
        type: string
        default: "false"
```

### 4. Branch-Based Optimization

- **Main branch**: Comprehensive checks ensure quality
- **Feature branches**: Fast feedback improves developer experience

### 5. Security Scanning

Trivy scans for:
- Dependency vulnerabilities
- Configuration issues
- Exposed secrets
Results uploaded to GitHub Security tab

### 6. Smart Docker Tagging

Uses `docker/metadata-action` for automatic tagging:
- Main branch: `latest`, `sha-{long-sha}`
- Feature branch: `develop-{branch-name}`, `sha-{long-sha}`
- Version tags: `v1.0.0` → `1.0.0`

## Usage

### For Developers

When you push to a feature branch:
1. Fast checks run (format, lint, tests without coverage)
2. Feedback in ~5-10 minutes
3. Docker image built with branch-specific tag

When you merge to main:
1. Comprehensive checks run (all pre-commit hooks)
2. Full test coverage required
3. Security scan
4. Docker image pushed as `latest`

### For Maintainers

#### Updating Python Version

Edit the `env` section in `main.yml`:
```yaml
env:
    PYTHON_VERSION: "3.13"  # Update here
```

#### Modifying Checks

Each check is isolated in its own workflow file. For example, to modify linting:
1. Edit `.github/workflows/lint.yml`
2. Changes apply to both main and feature branches

#### Adding New Checks

1. Create new workflow in `.github/workflows/`
2. Add `workflow_call` trigger
3. Download artifacts and restore environment
4. Add to `main-branch-ci.yml` and/or `feature-branch-ci.yml`

## Troubleshooting

### Artifact Not Found

If a job fails with "Artifact not found":
- Ensure `setup-environment.yml` completed successfully
- Check that artifact name matches: `solanaexporter-artifacts-{run_id}`

### Permission Errors in Virtual Environment

The restore-environment action automatically fixes permissions:
```bash
find "$VENV_DIR" -path "*/bin/*" -type f -exec chmod +x {} \;
```

### Composite Action Not Found

The workflows automatically copy `.github` directory from artifacts:
```bash
cp -r "$ARTIFACTS_PATH/github-actions" "$GITHUB_WORKSPACE/.github"
```

### Poetry Configuration Issues

Poetry is configured to use artifact directories:
```bash
poetry config virtualenvs.create false
poetry config virtualenvs.path "$ARTIFACTS_PATH/venv"
```

## Performance Comparison

### Old Setup (Traditional)
- Each job: ~3-5 minutes setup + 1-2 minutes execution
- Total for 6 jobs: ~24-42 minutes

### New Setup (Artifact-based)
- Setup job: ~4-6 minutes (one time)
- Each job: ~30 seconds restore + 1-2 minutes execution
- Total for 6 jobs: ~14-18 minutes

**Time savings: ~40-60% faster**

## Best Practices

1. **Keep artifacts small**: Only include necessary files
2. **Use caching**: Poetry cache significantly speeds up setup
3. **Parallel execution**: All jobs run in parallel after setup
4. **Branch-specific strategies**: Different needs for main vs features
5. **Non-blocking checks**: Use `continue-on-error` for optional checks

## Migration from Old CI

The old workflows have been completely replaced. The old approach was:
- Separate, independent jobs
- Each job ran `poetry install` independently
- No environment reuse
- Same checks for all branches

Benefits of new approach:
✅ Faster execution (artifact reuse)
✅ More maintainable (reusable workflows)
✅ Better developer experience (fast feedback on features)
✅ Enhanced security (vulnerability scanning)
✅ Smarter Docker tagging

## References

- [GitHub Actions: Reusable Workflows](https://docs.github.com/en/actions/using-workflows/reusing-workflows)
- [GitHub Actions: Composite Actions](https://docs.github.com/en/actions/creating-actions/creating-a-composite-action)
- [SupraExporter CI Setup](https://github.com/Blocksize-Capital-GmbH/SupraExporter)

