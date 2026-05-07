# Contributing to HubOS

Thanks for your interest in contributing! Here's how to get started.

## Quick Start

```bash
# 1. Fork & clone
git clone https://github.com/<your-username>/HubOS.git
cd HubOS

# 2. Set up Python environment
python3 -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -e ".[dev]"

# 3. Install pre-commit hooks
pre-commit install

# 4. Start dev server
hubos app --reload
```

## Development Workflow

1. **Create a branch** from `main`: `git checkout -b feat/your-feature`
2. **Make changes** and commit with clear messages
3. **Run checks** before pushing:
   ```bash
   pre-commit run --all-files          # Python lint
   cd console && npm run format:check  # TypeScript/Prettier
   ```
4. **Push and open a PR** against `main`

## Code Style

### Python
- **Formatter**: Black (auto-applied by pre-commit)
- **Linter**: flake8 + pylint
- **Type hints**: mypy (strict mode in CI)
- **Import order**: stdlib → third-party → local
- **Line length**: 88 chars (Black default)

### TypeScript / React
- **Formatter**: Prettier
- **Linter**: ESLint (via create-react-app)
- **Component style**: Functional components with hooks

### Commit Messages
Use conventional commit format:
```
feat: add new channel adapter for Slack
fix: resolve memory leak in session cleanup
docs: update API documentation for cron endpoints
chore: upgrade dependencies
```

## Project Structure

```
src/hubos/
├── app/            # FastAPI app, routers, channels
├── agents/         # Agent core, tools, skills
├── config/         # Configuration system
├── core/           # Memory, execution, work experience
├── cli/            # CLI commands
└── providers/      # LLM provider integrations

console/            # React frontend
```

## Adding a New Skill

1. Create `src/hubos/agents/skills/<skill_name>/`
2. Add a `SKILL.md` with usage instructions
3. Add Python scripts in a `scripts/` subdirectory
4. Register in the skill pool: `cp -r src/hubos/agents/skills/<skill_name> ~/.hubos/skill_pool/`
5. Update `~/.hubos/skill_pool/skill.json`

## Adding a New Channel

1. Create `src/hubos/app/channels/<channel_name>/`
2. Implement `channel.py` inheriting from `BaseChannel`
3. Add config class in `src/hubos/config/config.py`
4. Register in `src/hubos/app/channels/registry.py`

## Testing

```bash
# Run all tests
pytest

# Run a specific test file
pytest tests/test_config.py

# Run with coverage
pytest --cov=hubos
```

## Reporting Issues

- **Bug reports**: Use the Bug Report template
- **Feature requests**: Use the Feature Request template
- **Questions**: GitHub Discussions

## License

By contributing, you agree that your contributions will be licensed under the [Apache License 2.0](LICENSE).
