# Forward from Saved Messages

This script allows you to forward a message from your Saved Messages (me) directly to your target groups while preserving all formatting including Telegram Premium emojis.

## Usage

```bash
python scripts/forward_from_saved.py [options]
```

## Options

- `-id`, `--message-id` - ID of the message in Saved Messages. If not provided, uses the latest message.
- `--dry-run` - Show target groups without sending.
- `--limit` - Limit the number of target groups (for testing).
- `--reset-progress` - Reset progress before sending.

## How it works

1. Imports a message from your Saved Messages (`me`)
2. Processes any premium emojis and formatting
3. Downloads any media attached to the message
4. Sends the message to all your configured target groups

## Examples

Forward the latest message from Saved Messages:
```bash
python scripts/forward_from_saved.py
```

Forward a specific message by ID:
```bash
python scripts/forward_from_saved.py -id 12345
```

Test run without actually sending:
```bash
python scripts/forward_from_saved.py --dry-run
```