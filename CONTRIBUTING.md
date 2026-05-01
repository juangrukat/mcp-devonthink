# Contributing

Contributions are welcome.

## Development Setup

```bash
pip install -r requirements.txt
cp .env.example .env
```

## Validation

```bash
python3 -m compileall app main.py
python3 main.py --help
```

## Notes

- Target Python 3.11+.
- Keep changes focused and documented.
- Update README/docs when behavior changes.
