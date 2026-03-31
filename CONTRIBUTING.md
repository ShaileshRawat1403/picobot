# Contributing to Picobot

Thank you for your interest in contributing to Picobot!

## Development Setup

```bash
# Clone the repository
git clone https://github.com/picobot-ai/picobot.git
cd picobot

# Create virtual environment
python -m venv venv
source venv/bin/activate  # Linux/Mac
# or
.\venv\Scripts\activate  # Windows

# Install in development mode
pip install -e ".[dev]"
```

## Running Tests

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=picobot

# Run specific test file
pytest tests/test_telegram.py
```

## Code Style

We use `ruff` for linting:

```bash
# Check code style
ruff check .

# Auto-fix issues
ruff check --fix .
```

## Type Checking

```bash
mypy picobot
```

## Submitting Changes

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/my-feature`
3. Make your changes
4. Run tests: `pytest`
5. Commit your changes: `git commit -m "Add my feature"`
6. Push to your fork: `git push origin feature/my-feature`
7. Open a Pull Request

## Commit Messages

Please follow conventional commits:
- `feat:` for new features
- `fix:` for bug fixes
- `docs:` for documentation changes
- `test:` for test changes
- `refactor:` for code refactoring

## Questions?

Open an issue on GitHub for questions or discussions.
