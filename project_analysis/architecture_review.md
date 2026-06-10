# Architecture Review

## Summary

- Modules analyzed: 108
- Internal import edges: 276
- Package dependency edges: 87
- Cyclic import groups: 0

The project is split into clear domains: exchange adapters, market feature collection, signal detection, paper trading, data persistence, and Telegram UX. The highest architectural risk is the Telegram layer depending directly on repositories, market features, paper statistics, formatting, and mutable config writes. This makes Telegram callbacks a broad integration surface and raises regression risk when adding new UI actions.

## Dependency Hotspots

| Module | LOC | Classes | Functions | Fan-in | Fan-out |
|---|---:|---:|---:|---:|---:|
| `app.config` | 428 | 26 | 14 | 37 | 0 |
| `app.data.database` | 240 | 1 | 9 | 25 | 1 |
| `app.market.features` | 244 | 2 | 18 | 17 | 6 |
| `app.data.models` | 370 | 21 | 0 | 20 | 1 |
| `app.data.repositories` | 715 | 9 | 44 | 19 | 2 |
| `app.utils.time` | 8 | 0 | 3 | 19 | 0 |
| `app.main` | 303 | 0 | 9 | 0 | 17 |
| `app.telegram.commands` | 806 | 1 | 55 | 2 | 15 |
| `app.backtesting.engine` | 202 | 1 | 7 | 5 | 9 |
| `app.signals.signal_engine` | 329 | 2 | 14 | 2 | 11 |

## Package Dependencies

| From | To | Imports |
|---|---|---:|
| `app.services` | `app.data` | 15 |
| `tests` | `app.config` | 8 |
| `app.strategies` | `app.market` | 7 |
| `app.telegram` | `app.data` | 7 |
| `app.paper` | `app.data` | 6 |
| `app.web` | `app.services` | 6 |
| `app.backtesting` | `app.data` | 5 |
| `app.jobs` | `app.data` | 5 |
| `app.market` | `app.exchanges` | 5 |
| `app.telegram` | `app.services` | 5 |
| `tools` | `app.data` | 5 |
| `app.optimization` | `app.backtesting` | 4 |
| `app.paper` | `app.config` | 4 |
| `app.signals` | `app.config` | 4 |
| `app.signals` | `app.data` | 4 |
| `app.strategies` | `app.config` | 4 |
| `tests` | `app.paper` | 4 |
| `tests` | `app.signals` | 4 |
| `tools` | `app.config` | 4 |
| `app.analysis` | `app.data` | 3 |
| `app.main` | `app.utils` | 3 |
| `app.main` | `app.market` | 3 |
| `app.market` | `app.utils` | 3 |
| `app.ml` | `app.data` | 3 |
| `app.paper` | `app.utils` | 3 |
| `app.services` | `app.config` | 3 |
| `app.services` | `app.utils` | 3 |
| `app.signals` | `app.market` | 3 |
| `app.telegram` | `app.config` | 3 |
| `tests` | `app.telegram` | 3 |
| `tests` | `app.market` | 3 |
| `app.backtesting` | `app.utils` | 2 |
| `app.data` | `app.utils` | 2 |
| `app.exchanges` | `app.utils` | 2 |
| `app.jobs` | `app.backtesting` | 2 |
| `app.main` | `app.signals` | 2 |
| `app.main` | `app.exchanges` | 2 |
| `app.main` | `app.data` | 2 |
| `app.market` | `app.logger` | 2 |
| `app.market` | `app.data` | 2 |
| `app.optimization` | `app.data` | 2 |
| `app.paper` | `app.logger` | 2 |
| `app.services` | `app.paper` | 2 |
| `app.signals` | `app.logger` | 2 |
| `app.signals` | `app.utils` | 2 |
| `app.telegram` | `app.market` | 2 |
| `app.telegram` | `app.utils` | 2 |
| `app.telegram` | `app.signals` | 2 |
| `app.web` | `app.data` | 2 |
| `app.web` | `app.jobs` | 2 |
| `app.web` | `app.config` | 2 |
| `tests` | `app.backtesting` | 2 |
| `tests` | `app.exchanges` | 2 |
| `tests` | `app.strategies` | 2 |
| `tools` | `app.backtesting` | 2 |
| `app.analysis` | `app.logger` | 1 |
| `app.analysis` | `app.utils` | 1 |
| `app.backtesting` | `app.exchanges` | 1 |
| `app.backtesting` | `app.strategies` | 1 |
| `app.backtesting` | `app.config` | 1 |
| `app.backtesting` | `app.market` | 1 |
| `app.backtesting` | `app.jobs` | 1 |
| `app.exchanges` | `app.logger` | 1 |
| `app.jobs` | `app.optimization` | 1 |
| `app.jobs` | `app.config` | 1 |
| `app.jobs` | `app.utils` | 1 |
| `app.jobs` | `app.ml` | 1 |
| `app.main` | `app.analysis` | 1 |
| `app.main` | `app.logger` | 1 |
| `app.main` | `app.config` | 1 |
| `app.main` | `app.telegram` | 1 |
| `app.main` | `app.paper` | 1 |
| `app.market` | `app.config` | 1 |
| `app.optimization` | `app.config` | 1 |
| `app.services` | `app.strategies` | 1 |
| `app.signals` | `app.ml` | 1 |
| `app.signals` | `app.strategies` | 1 |
| `app.signals` | `app.exchanges` | 1 |
| `app.signals` | `app.telegram` | 1 |
| `app.signals` | `app.paper` | 1 |
| `app.telegram` | `app.logger` | 1 |
| `app.telegram` | `app.paper` | 1 |
| `tests` | `tools` | 1 |
| `tests` | `app.data` | 1 |
| `tests` | `app.web` | 1 |
| `tools` | `app.jobs` | 1 |
| `tools` | `app.utils` | 1 |

## Review Notes

- Keep exchange clients free of private trading endpoints while paper mode is enabled.
- Keep Telegram callback handlers thin; move settings writes and signal mutations behind application services.
- Keep rolling market buffers bounded and verify stale-data handling when adding Hyperliquid.
- Treat unused-file and unused-function results as review candidates, not automatic deletion targets.

## Recommended Next Steps

1. Break the `app.telegram.commands` to `app.telegram.bot` type-import cycle with `TYPE_CHECKING`.
2. Move settings mutation/persistence into a small config service with explicit save-result handling.
3. Add integration tests for Telegram callbacks that mutate signal status or config.
4. Add architecture guardrails once module boundaries stabilize, for example no imports from `app.telegram` into market/data/signal modules.
