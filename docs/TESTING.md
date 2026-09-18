# Testing

## The rule

**No test may reach the real data adapter.** A test that goes to the network passes or
fails for a reason nobody chose — the market moved, a provider rate-limited, a key was or
was not in someone's shell — and a suite like that reports the weather rather than the
code. So `tests/conftest.py` replaces, for the whole session, every factory that could put
a live provider in a test's hands (`data.provider.select_market_adapter`,
`pipeline._build_adapter`, `data.sentiment.build_sentiment_adapter`) with one that raises
`test reached the real data adapter; inject a fake`, naming the factory and the test that
tripped it. A test that needs data **injects a fake adapter**; a test that genuinely
exercises the provider **says so in writing** with `@pytest.mark.real_adapter`; and
nothing else is an acceptable way past the guard — loosening an assertion, or marking a
test that is not actually about the adapter, converts a real finding into a hidden one.

## Opting out

```python
@pytest.mark.real_adapter
def test_provider_selection_default_is_yfinance(monkeypatch):
    pytest.importorskip("yfinance")
    ...
```

The marker is registered in `pyproject.toml` and the suite runs with `--strict-markers`,
so a typo (`real_adaptor`) is an error rather than a silent no-op — a silent no-op here
means the test reaches the network after all.

The marker does **not** skip on its own. A test that needs an optional dependency declares
it the way the rest of the repo does, with `pytest.importorskip` (49 uses for `streamlit`,
2 for `yfinance`, one each for `pandas`, `markdown`, `xhtml2pdf`). That is deliberate:
`test_provider_selection_eodhd` builds an EODHD adapter and
`test_provider_selection_unknown_raises` builds nothing at all, so neither should be
skipped on a box without yfinance just because it shares a marker with two that do.

Five tests carry the marker today — the provider-selection cases in
`tests/test_eodhd_adapter.py` and `tests/test_hybrid_adapter.py`. Constructing an adapter
opens no connection; they are caught because the guard guards construction, which is the
only place it can catch the tests that should not be constructing.

## Credentials

The suite does not read your keys. Two mechanisms, because there are two moments:

- an autouse fixture drops `ANTHROPIC_API_KEY`, `FINNHUB_API_KEY` and `EODHD_API_KEY`
  before each test and again after it;
- `dotenv.load_dotenv` reads nothing for the duration of the run.

Both are needed. `tests/test_app.py` drives the Streamlit app through `AppTest`, and
`app.py`'s `main()` loads the local `.env` — correctly; that is what it is for — so the
keys arrive *during* a test, which no amount of teardown can undo. Before this guard they
then stayed in `os.environ` for the rest of the session, and **27 tests were handed a live
Finnhub client where CI hands them an honest absence**. One of them,
`test_sentiment_provider_status_is_logged`, asserts the "no `FINNHUB_API_KEY` set" wording
and had been taking the other branch entirely. The suite behaved differently on the
owner's machine than in CI, and read as green either way.

`app.py` is unchanged and still loads your `.env` when you actually run it.

A test that wants a key present sets one with `monkeypatch.setenv` and gets exactly the
value it chose — monkeypatch unwinds before the fixture does.

## Running

```
python -m pytest
```

`pyproject.toml` already supplies `-q`, so adding another `-q` makes it `-qq` and
suppresses the summary line. **Run the full suite before every commit, including docs-only
commits** (CLAUDE.md rule 6; imports break through refactors and this repo has the scar).

The guard is also why the suite got faster: 106s before it, 57s after. Half the wall clock
was a live Finnhub client nobody had asked for.
