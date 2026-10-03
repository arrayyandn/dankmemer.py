# dankmemer.py

[Documentation](https://dankmemerpy.readthedocs.io/en/latest/)

An asynchronous Python library for the official Dank Memer API.

Look up items by name, explore fishing data, check daily gifts and merchant
trades, and receive events when the data changes. The library works on its own
or alongside discord.py.

## Installation

Python 3.11 or newer and a Dank Memer developer token are required.
These examples use the official API client introduced in **1.0.0**.

```console
python -m pip install --upgrade "dankmemer.py>=1.0.0"
```

The same releases are available under the mirror name `dankmemer`. Install
one of the two package names in each environment; both import as `dankmemer`.

For persistent event delivery, install the backend you want to use:

```console
python -m pip install --upgrade "dankmemer.py[sqlite]>=1.0.0"
python -m pip install --upgrade "dankmemer.py[postgres]>=1.0.0"
```

Create your API token in the [developer dashboard](https://dankmemer.lol/dashboard/developers).
The library imports as `dankmemer`. For Discord slash commands and item
autocomplete, install `"dankmemer.py[discord]>=1.0.0"` with the same pip options.

## Quick start

Set `DANK_MEMER_API_TOKEN` in your environment, then save this as `launcher.py`:

```python
import asyncio
import os

from dankmemer import DankMemer


async def main() -> None:
    async with DankMemer(os.environ["DANK_MEMER_API_TOKEN"]) as dank:
        item = await dank.items.get("Life Saver")
        if item is not None:
            print(f"{item.name}: market value {item.market_value:,}")


if __name__ == "__main__":
    asyncio.run(main())
```

Run `python launcher.py`. Keep one client open for your application's lifetime;
the context manager closes it when you finish. Item lookups share a catalog
cached for one hour by default. Use `refresh=True` to request an updated value.

## What you can do

- Find items, pets, skins, commands, and fishing records by name or ID.
- Search and filter catalogs, and resolve related records.
- Add item-name choices to discord.py slash commands with an optional helper.
- Read active boosts, drops, sales, fishing events, and the trending game.
- Check daily gifts, merchant trades, and completed lottery results.
- Browse blogs and changelogs with asynchronous pagination.
- Subscribe to events with resource-specific polling schedules.
- Preserve detected callbacks across restarts with SQLite, PostgreSQL, or a custom store.
- Share polling and delivery between processes when your application needs it.

## Events

Register async handlers before starting the client:

```python
from dankmemer import DankMemer, Drop, EventConfig

dank = DankMemer(token, events=EventConfig(emit_initial=True))


@dank.event
async def on_drop_started(drop: Drop) -> None:
    print(f"Drop {drop.id} is available until {drop.ends_at}")
```

Start the client and keep it open to receive events. Only resources required
by registered listeners are polled. Automatic attempts to the same resource
remain at least 60 seconds apart.

Events describe changes observed through polling. A short-lived record can be
missed if it disappears between reads; durable delivery preserves changes the
library has already detected.

## Documentation

The [documentation](https://dankmemerpy.readthedocs.io/en/latest/) includes:

- A quick start and standalone examples.
- Slash command autocomplete, function examples, and a Bot with commands in Cogs.
- Every event signature, its meaning, and its polling schedule.
- Caching, error handling, explicit startup, and persistent delivery.
- The public client, resource, model, configuration, and storage references.

Use the [official API documentation](https://dankmemer.lol/dashboard/developers/docs)
alongside the library guides. API access must follow Dank Memer's applicable rules,
[terms](https://dankmemer.lol/legal/terms), and [privacy policy](https://dankmemer.lol/legal/privacy).

Upgrading from rc3? Follow the
[migration guide](https://dankmemerpy.readthedocs.io/en/latest/migrating.html)
and [release notes](https://dankmemerpy.readthedocs.io/en/latest/changelog.html).

Report problems through [GitHub issues](https://github.com/arrayyandn/dankmemer.py/issues).
For working on the library itself, see [Contributing](CONTRIBUTING.md).

## Acknowledgements

The enum design and event/listener API were inspired by
[discord.py](https://github.com/Rapptz/discord.py).
This project is independent and uses its own implementation.

## License

MIT. See [LICENSE](LICENSE).
