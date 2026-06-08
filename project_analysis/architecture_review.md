# Architecture Review

## Summary

- Modules analyzed: 69
- Internal import edges: 175
- Package dependency edges: 59
- Cyclic import groups: 0

The project is split into clear domains: exchange adapters, market feature collection, signal detection, paper trading, data persistence, and Telegram UX. The highest architectural risk is the Telegram layer depending directly on repositories, market features, paper statistics, formatting, and mutable config writes. This makes Telegram callbacks a broad integration surface and raises regression risk when adding new UI actions.

## Dependency Hotspots

| Module | LOC | Classes | Functions | Fan-in | Fan-out |
|---|---:|---:|---:|---:|---:|
| `app.config` | 309 | 16 | 14 | 23 | 0 |
| `app.data.models` | 224 | 13 | 0 | 16 | 1 |
| `app.main` | 298 | 0 | 9 | 0 | 17 |
| `app.data.database` | 161 | 1 | 8 | 15 | 1 |
| `app.telegram.commands` | 722 | 1 | 53 | 2 | 14 |
| `app.utils.time` | 8 | 0 | 3 | 16 | 0 |
| `app.data.repositories` | 456 | 4 | 27 | 11 | 2 |
| `app.signals.signal_engine` | 257 | 2 | 10 | 2 | 11 |
| `app.paper.manager` | 510 | 2 | 22 | 4 | 8 |
| `app.telegram.bot` | 162 | 1 | 10 | 3 | 9 |

## Package Dependencies

| From | To | Imports |
|---|---|---:|
| `app.services` | `app.data` | 12 |
| `app.telegram` | `app.data` | 7 |
| `app.paper` | `app.data` | 6 |
| `tests` | `app.config` | 6 |
| `app.web` | `app.services` | 5 |
| `app.market` | `app.exchanges` | 4 |
| `app.paper` | `app.config` | 4 |
| `app.signals` | `app.data` | 4 |
| `app.signals` | `app.config` | 4 |
| `app.telegram` | `app.services` | 4 |
| `tests` | `app.paper` | 4 |
| `tests` | `app.signals` | 4 |
| `app.analysis` | `app.data` | 3 |
| `app.main` | `app.market` | 3 |
| `app.main` | `app.utils` | 3 |
| `app.market` | `app.utils` | 3 |
| `app.paper` | `app.utils` | 3 |
| `app.services` | `app.utils` | 3 |
| `app.signals` | `app.market` | 3 |
| `app.telegram` | `app.config` | 3 |
| `tests` | `app.telegram` | 3 |
| `app.data` | `app.utils` | 2 |
| `app.exchanges` | `app.utils` | 2 |
| `app.main` | `app.data` | 2 |
| `app.main` | `app.signals` | 2 |
| `app.main` | `app.exchanges` | 2 |
| `app.market` | `app.logger` | 2 |
| `app.market` | `app.data` | 2 |
| `app.paper` | `app.logger` | 2 |
| `app.services` | `app.paper` | 2 |
| `app.services` | `app.config` | 2 |
| `app.signals` | `app.utils` | 2 |
| `app.signals` | `app.logger` | 2 |
| `app.telegram` | `app.market` | 2 |
| `app.telegram` | `app.utils` | 2 |
| `app.telegram` | `app.signals` | 2 |
| `tools` | `app.data` | 2 |
| `app.analysis` | `app.utils` | 1 |
| `app.analysis` | `app.logger` | 1 |
| `app.exchanges` | `app.logger` | 1 |
| `app.main` | `app.logger` | 1 |
| `app.main` | `app.telegram` | 1 |
| `app.main` | `app.config` | 1 |
| `app.main` | `app.paper` | 1 |
| `app.main` | `app.analysis` | 1 |
| `app.market` | `app.config` | 1 |
| `app.signals` | `app.exchanges` | 1 |
| `app.signals` | `app.paper` | 1 |
| `app.signals` | `app.telegram` | 1 |
| `app.telegram` | `app.logger` | 1 |
| `app.telegram` | `app.paper` | 1 |
| `app.web` | `app.data` | 1 |
| `app.web` | `app.config` | 1 |
| `tests` | `app.exchanges` | 1 |
| `tests` | `tools` | 1 |
| `tests` | `app.data` | 1 |
| `tests` | `app.web` | 1 |
| `tools` | `app.config` | 1 |
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
