# Contributing to NibCast

Thank you for your interest in contributing!

## Getting Started

1. Fork the repo and clone it locally
2. Create a virtual environment: `python -m venv venv && venv\Scripts\activate`
3. Install dependencies: `pip install -r requirements.txt`
4. Run the app: `python main.py`

## Development Workflow

- Make changes in a feature branch
- Test manually: run the app, verify the feature works end-to-end
- Keep PRs focused — one feature or fix per PR

## Adding a New ASR/LLM Backend

1. Add config keys in `config.py` under the appropriate section
2. Add a handler in `transcriber.py` (`_call()` for ASR) or `text_processor.py`
3. Add the backend option to `_PERSISTED_KEYS` in `config.py`
4. Add the GET/POST handling in `web_dashboard.py` → `api_get_config` / `api_save_config`
5. Add the UI in `templates/dashboard.html` (backend pills + field section)
6. Add load/save in `static/dashboard.js` (`loadConfig` / `saveConfig`)

## Code Style

- Python: follow existing patterns (no docstrings, minimal comments)
- JavaScript: vanilla JS, no frameworks
- CSS: BEM-ish class names with the `vf-` prefix for global scope

## Reporting Issues

Please include:
- OS and Python version
- The error from the terminal (not just "it doesn't work")
- Steps to reproduce

## Security

**Never commit API keys.** The `.gitignore` excludes `config.json` and `.env` files.
If you accidentally commit secrets, rotate them immediately.
