# Architecture Review

## Summary

- Modules analyzed: 47
- Internal import edges: 112
- Package dependency edges: 41
- Cyclic import groups: 1

The project is split into clear domains: exchange adapters, market feature collection, signal detection, paper trading, data persistence, and Telegram UX. The highest architectural risk is the Telegram layer depending directly on repositories, market features, paper statistics, formatting, and mutable config writes. This makes Telegram callbacks a broad integration surface and raises regression risk when adding new UI actions.

## Dependency Hotspots

| Module | LOC | Classes | Functions | Fan-in | Fan-out |
|---|---:|---:|---:|---:|---:|
| `app.config` | 156 | 14 | 9 | 16 | 0 |
| `app.main` | 146 | 0 | 3 | 0 | 14 |
| `app.telegram.bot` | 149 | 1 | 10 | 4 | 9 |
| `app.utils.time` | 8 | 0 | 3 | 13 | 0 |
| `app.signals.signal_engine` | 151 | 2 | 9 | 1 | 11 |
| `app.market.features` | 200 | 2 | 14 | 7 | 4 |
| `app.telegram.commands` | 432 | 1 | 35 | 1 | 10 |
| `app.data.models` | 176 | 10 | 0 | 9 | 1 |
| `app.paper.manager` | 277 | 2 | 16 | 2 | 8 |
| `app.data.database` | 58 | 1 | 6 | 7 | 1 |

## Package Dependencies

| From | To | Imports |
|---|---|---:|
| `app.paper` | `app.data` | 6 |
| `app.telegram` | `app.data` | 6 |
| `app.market` | `app.exchanges` | 4 |
| `app.paper` | `app.config` | 4 |
| `app.signals` | `app.data` | 4 |
| `app.signals` | `app.config` | 4 |
| `app.main` | `app.market` | 3 |
| `app.market` | `app.utils` | 3 |
| `app.paper` | `app.utils` | 3 |
| `app.signals` | `app.market` | 3 |
| `app.telegram` | `app.config` | 3 |
| `tests` | `app.signals` | 3 |
| `tests` | `app.config` | 3 |
| `app.data` | `app.utils` | 2 |
| `app.exchanges` | `app.utils` | 2 |
| `app.main` | `app.signals` | 2 |
| `app.main` | `app.utils` | 2 |
| `app.main` | `app.exchanges` | 2 |
| `app.market` | `app.logger` | 2 |
| `app.market` | `app.data` | 2 |
| `app.signals` | `app.logger` | 2 |
| `app.signals` | `app.utils` | 2 |
| `app.telegram` | `app.utils` | 2 |
| `app.telegram` | `app.market` | 2 |
| `app.telegram` | `app.signals` | 2 |
| `tests` | `app.telegram` | 2 |
| `app.exchanges` | `app.logger` | 1 |
| `app.main` | `app.telegram` | 1 |
| `app.main` | `app.config` | 1 |
| `app.main` | `app.paper` | 1 |
| `app.main` | `app.logger` | 1 |
| `app.main` | `app.data` | 1 |
| `app.market` | `app.config` | 1 |
| `app.paper` | `app.logger` | 1 |
| `app.signals` | `app.telegram` | 1 |
| `app.signals` | `app.paper` | 1 |
| `app.signals` | `app.exchanges` | 1 |
| `app.telegram` | `app.logger` | 1 |
| `app.telegram` | `app.paper` | 1 |
| `tests` | `app.paper` | 1 |
| `tests` | `app.data` | 1 |

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
