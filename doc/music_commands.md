# Commands

Muse is controlled with `m.`-prefixed text commands (e.g. `m.play`) instead of Discord slash commands. Commands are read from any message that starts with `m.`, including messages sent by other bots — the only messages Muse ignores are its own.

`[--flag]` options are boolean toggles typed literally, e.g. `m.play some song --immediate --shuffle`. Required true/false values (the `config` toggles) are typed directly as `true`/`false`/`yes`/`no`/`on`/`off`/`1`/`0`.

Commands marked **Requires VC** need you to be in a voice channel first.

## Playback

| Command | Requires VC | Description |
|---|---|---|
| `m.play <query> [--immediate] [--shuffle] [--split] [--skip]` | Yes | Play a song (YouTube/Spotify URL or search query). Flags: add to front of queue, shuffle multi-track input, split chaptered tracks, skip current track |
| `m.pause` | Yes | Pause the current song |
| `m.resume` | Yes | Resume playback |
| `m.stop` | Yes | Stop playback, disconnect, and clear the queue |
| `m.disconnect` | Yes | Pause and disconnect Muse |
| `m.replay` | Yes | Replay the current song from the start |
| `m.seek <time>` | Yes | Seek to a position from the start of the song (e.g. `1m`, `30s`, `100`) |
| `m.fseek <time>` | Yes | Seek forward from the current position |
| `m.volume <level>` | Yes | Set the current player volume (0-100) |
| `m.now-playing` | No | Show the currently playing song |

## Queue

| Command | Requires VC | Description |
|---|---|---|
| `m.queue [page] [page-size]` | No | Show the current queue |
| `m.skip [number]` | Yes | Skip the next song(s) [default: 1] |
| `m.next [number]` | Yes | Alias of `skip` |
| `m.unskip` | Yes | Go back one song in the queue |
| `m.remove [position] [range]` | No | Remove song(s) from the queue [defaults: position 1, range 1] |
| `m.move <from> <to>` | No | Move a song within the queue |
| `m.shuffle` | Yes | Shuffle the current queue |
| `m.clear` | Yes | Clear the queue (keeps the currently playing song) |
| `m.loop` | Yes | Toggle looping the current song |
| `m.loop-queue` | Yes | Toggle looping the entire queue |

## Favorites

| Command | Requires VC | Description |
|---|---|---|
| `m.favorites use <name> [--immediate] [--shuffle] [--split] [--skip]` | Yes | Queue a saved favorite |
| `m.favorites list` | No | List all favorites |
| `m.favorites create <name> <query>` | No | Save a new favorite |
| `m.favorites remove <name>` | No | Remove a favorite (own favorites, or any if you're the server owner) |

## Server config

`m.config <subcommand> ...` — requires the Manage Guild permission.

| Subcommand | Description |
|---|---|
| `set-playlist-limit <limit>` | Max tracks added from a playlist |
| `set-wait-after-queue-empties <seconds>` | Delay before leaving voice when the queue empties (0 = never leave) |
| `set-leave-if-no-listeners <true\|false>` | Leave when everyone else leaves the channel |
| `set-queue-add-response-hidden <true\|false>` | Whether "added to queue" replies are hidden |
| `set-reduce-vol-when-voice <true\|false>` | Auto-reduce volume when people speak |
| `set-reduce-vol-when-voice-target <0-100>` | Target volume % when people speak |
| `set-auto-announce-next-song <true\|false>` | Auto-announce the next queued song |
| `set-default-volume <0-100>` | Default volume on join |
| `set-default-queue-page-size <1-30>` | Default page size for `m.queue` |
| `get` | Show all current settings |
