# Contributing to MyGG

Thanks for considering a contribution. MyGG is small, opinionated, and
meant to stay readable — every contribution should leave the codebase
calmer than it found it.

By participating you agree to abide by the [Code of Conduct](CODE_OF_CONDUCT.md).

## Reporting bugs

Open a [GitHub issue](https://github.com/pedropedropedropedrop/MyGG/issues/new/choose) using the **Bug Report**
template. Please include:

- What you expected to happen.
- What actually happened.
- A minimal reproduction with sample text, the selected chat backend,
  Python version and OS.
- Relevant redacted error lines. Remove tokens, emails, message contents and personal IDs.

## Suggesting features

Open a **Feature Request** issue. The most useful feature requests:

- Describe the user-facing problem first ("I want to … but currently …").
- Sketch one possible solution (it's fine to be wrong).
- Note alternatives you considered.
- Indicate whether you'd be willing to implement it.

## Pull request flow

1. Fork the repo and create a feature branch:
   ```bash
   git checkout -b feature/short-name
   ```
2. Make small, focused commits. Use [conventional commits](https://www.conventionalcommits.org/)
   when it's natural (`feat:`, `fix:`, `docs:`, `refactor:`, `test:`),
   but a clear plain-English message is fine too.
3. Use the project's virtual-environment Python to install the test runner and check your changes.
   ```bash
   python -m pip install pytest
   python -m pytest tests/
   python validate.py
   ```
4. Push and open a PR against `main`. Use the PR template.

A maintainer will review. Expect comments — the goal is good code, not
fast merges.

## Local dev setup

Follow [ONBOARDING.md](ONBOARDING.md) once to get a working bot. Then:

- Use `.\venv\Scripts\python.exe` on Windows or activate the venv with `source venv/bin/activate` on macOS/Linux.
- Run `python main.py` to start the bot in the foreground.
- Edit code; restart the process to pick up changes.
- For module-only changes, `python validate.py` is a fast sanity check.

## Code style

- **Python 3.12** — use modern syntax (`match/case`, `int | None`, etc.).
- **Black-compatible formatting** (88 cols soft, 100 hard).
- **Type hints encouraged** for function signatures and return values.
  Internal helpers don't need them, but anything called across modules
  should.
- **No emojis in code** unless the user asked for them.
- **Constants live in `constants.py`** — no magic numbers in source files.
- **Logging is `logging`, not `print`**. Module-level
  `logger = logging.getLogger(__name__)`.
- **NASA/JPL "Power of Ten" hints** apply where they make sense:
  - All loops have an upper bound (no `while True:` without a counter).
  - All memory allocations are bounded (caches evict).
  - No hidden assertions — use `assert` for invariants you actually
    want to crash on.
  - All function returns within a reasonable size (split when growing).
- Module docstring at the top of every file.

If you're adding something user-facing, update:

- `README.md` (feature list).
- `ONBOARDING.md` (setup, if it needs config).
- `.env.example` (any new env vars, with comments).
- `CHANGELOG.md` (under "Unreleased").
- `ARCHITECTURE.md` (if the change shifts how the system fits together).

## Adding a new module

A "module" is a self-contained capability — Whoop, Weather, etc. To add one:

1. Copy `modules/_template.py` to `modules/your_module.py`.
2. Define your tool schemas in `TOOL_DEFINITIONS`.
3. Implement `async execute_tool(name, input_data)` and return `str`.
4. Optionally implement `async heartbeat_contributions()` returning a
   one-line briefing summary or `None`.
5. Restart the bot. Auto-discovery picks it up — you should see
   `Loaded modules: ..., your_module` in the logs.
6. Verify with `python validate.py`.
7. If you need new env vars, add them to `.env.example` with a comment.
8. Add a short paragraph to README's feature list and ONBOARDING's
   "Optional features" if it isn't on by default.

### Module checklist

- [ ] Single file in `modules/your_module.py`.
- [ ] Module name is descriptive and lowercase.
- [ ] All tools start with a clear verb (`get_*`, `list_*`, `add_*`).
- [ ] Tool schemas use the same patterns as core tools (see `agent.py`).
- [ ] Errors are caught and returned as strings — never raised through
      the agent loop.
- [ ] Long blocking I/O is wrapped in `asyncio.to_thread`.
- [ ] No secrets in source — read from `os.getenv`.
- [ ] Tests in `tests/test_your_module.py` if the logic is non-trivial.

## Tests

The regression suite lives in `tests/`. Add a focused case when changing access rules, model routing, or module behavior.

For the synthetic tool-routing evaluation, run `python skillopt/eval_routing.py --selftest` to check the scorer offline. To exercise the configured model, run `python skillopt/eval_routing.py --limit 3`. The live evaluation asks the model for tool choices without dispatching those actions. Cloud runs use your API account.

Run with:

```bash
python -m pytest tests/
```

## Final note

If you're not sure, open an issue first and ask. Drive-by PRs are
welcome but a quick chat saves everyone time when the change is
something larger than a typo fix.
