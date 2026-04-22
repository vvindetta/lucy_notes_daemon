![lucy.png](media/lucy.png)

# Lucy d(a)emon — modular notes manager

Your notes are just files. Use any editor you like. No editor plugins. Git is your cloud.

Lucy daemon monitors your note folder. Every time you edit something, it runs modules on that file (formatter, sync, git, etc).

Lucy can also read Unix-style flags written inside the note file and pass them to modules.
It could be an execution command or some settings.

## Example of use
If `README.md` is one of your notes, you can write command flags directly inside it:

Rename `README.md` to `DONOTreadme.md`:

```--r DONOTreadme.md```

Execute the terminal command (output will be written directly to the file):

```--c neofetch```


Then press ```CTRL+S``` - Lucy will detect the change and run the modules.

### Use cases  
- Auto-format files, rename and sort it
- Sync notes between formats and programs
- Git auto-commit
- Calendar integration
- Sync your system widgets: [KDE Plasma demo](media/plasma_sync.mp4)
- Write your own module!

### How to sync with mobile?
Use Lucy's Git module together with [GitSync Android app](https://github.com/ViscousPot/GitSync) and [Markor](https://github.com/gsantner/markor) text editor.

## Theory

### Flags system
You can provide flags in three places:

1. Inside the note file (for per-note behavior)
2. In config.txt (global defaults)
3. At startup: ```python3 main.py --some-flag```

### System module

```--help``` for help message: 
```
* --mods: print loaded modules and their priorities
* --config: print config values that differ from defaults
* --man list: print all arguments (no descriptions)
* --man full: print all arguments with descriptions
* --man <name>: print one argument with description (example: --man todo)
```


```--mods``` to see loaded modules:
```
* sys (0)
* banner (10)
* todo (10)
* renamer (20)
* plasma_sync (30)
* cmd (50)
```

```--man list``` for list all flag arguments.

`--man flag_arg_here` for help with any flag argument.

```--man man``` :

```
* --man: Argument manual. Use: --man list OR --man full OR --man <name> (example: --man todo). (type=str, default=None)
```

## Install

Tested on Fedora GNU+Linux.

1. Clone the repository:
```
git clone https://codeberg.org/Vindetta/lucy_notes_daemon && cd lucy_notes_daemon
```
   
2. Install dependencies:
```
pip install -r requirements.txt
```

3. Setup ```--sys-notes-dirs``` in ```config.txt```

4. Run the program:
```
python3 main.py
```

**Turn on file auto-update in your text editor!**

## Modules

To add new modules, you need to edit the list in `main.py`.
Hot reload and install/uninstall commands are in the roadmap. Sorry.

### List of available modules

**Basic:**
- `sys`: runtime information, man(ual) messages
- `todo_formatter`: format points to Markdown-style checkboxes  
  `- point` → `- [ ] point`
- `banner`: prints an ASCII banner with the current date or custom text
- `renamer`: renames a file using `--r name`

**Experimental (disabled by default):**
- `git`: auto commit, pull, push, etc. Please install GIT cli. 
- `plasma_sync`: sync KDE Plasma widgets ([see video](media/plasma_sync.mp4))
- `cmd`: run a terminal command with `--c command`.  
  Cmd module may cause security issues when used with the `git`.

## Git module layout

The `git` module lives in `lucy_notes_manager/modules/git/` and is split into
small files by responsibility:

```
modules/git/
├── __init__.py   # re-exports `Git` and `_RepoBatch`
├── module.py     # `Git` class: event hooks, batching, worker loop
├── batch.py      # `_RepoBatch` dataclass
├── paths.py      # path helpers + GIT_SSH_COMMAND construction
├── commands.py   # thin subprocess wrappers around `git`
├── parsing.py    # pure parsers (`status --porcelain`, conflict markers, push errors)
├── conflicts.py  # auto-resolve merge conflicts (`ours`, `theirs`, `union`)
└── pull.py       # high-level `pull --no-rebase` + conflict recovery
```

## Repository-update notifications

The `git` module can pull on real remote updates instead of polling on
every file-open. Set `--git-update-source` to one of `poll` (default),
`rss`, `webhook`, or `off`.

- **`poll`** (default): the existing behaviour — pull when a file inside
  the repo is opened, throttled by a growing cooldown.
- **`rss`**: a background thread polls an RSS/Atom feed per repository
  and triggers a pull when a new entry appears.
- **`webhook`**: a local HTTP server receives push events and pulls the
  matching repository after verifying the request signature.
- **`off`**: never auto-pull; commit and push are still performed.

Providers are selected per repository via a JSON config file passed with
`--sys-notifications-config /path/to/notifications.json`. An example file
is in [`notifications.example.json`](notifications.example.json).

### Supported platforms

| Platform | Webhook signature         | RSS/Atom feed example                                         |
| -------- | ------------------------- | ------------------------------------------------------------- |
| GitHub   | `X-Hub-Signature-256`     | `https://github.com/<owner>/<repo>/commits/<branch>.atom`     |
| Gitea    | `X-Gitea-Signature` (HMAC-SHA256, same scheme as GitHub) | `https://gitea.example/<owner>/<repo>/atom/branch/<branch>` |
| Forgejo  | identical to Gitea        | identical to Gitea                                            |
| GitLab   | `X-Gitlab-Token` (plain)  | `https://gitlab.example/<group>/<repo>/-/commits/<branch>?format=atom` |

New platforms can be added by calling
`lucy_notes_manager.notifications.register_provider(name, cls)`.

### Webhook configuration

Each repository needs a shared secret and a `webhook_id` in the form
`<platform>:<owner>/<name>` that matches `payload.repository.full_name`
(or `path_with_namespace` for GitLab):

```json
{
  "repo_root": "/home/user/notes",
  "platform": "github",
  "transport": "webhook",
  "secret": "long-random-string",
  "branch": "main",
  "extra": {"webhook_id": "github:owner/notes"}
}
```

Startup flags control the HTTP server:

- `--sys-webhook-host` (default `127.0.0.1`)
- `--sys-webhook-port` (default `8765`)
- `--sys-webhook-path` (default `/webhook`)

**Security notes**
- Requests without a valid signature are rejected with HTTP 401. There
  is no anonymous/default-allow mode.
- The server binds to `127.0.0.1` by default — put a reverse proxy (e.g.
  nginx with TLS) in front of it before exposing to the public internet.
- Request bodies above 2 MiB are rejected to avoid memory abuse.
- Only push events are acted on; other events (ping, issues, …) are
  ignored with HTTP 204.

### RSS configuration

```json
{
  "repo_root": "/home/user/public-notes",
  "platform": "github",
  "transport": "rss",
  "feed_url": "https://github.com/owner/public-notes/commits/main.atom",
  "poll_interval_sec": 300
}
```

- The first successful poll establishes a baseline; no event fires.
- Subsequent polls fire one event whenever the feed contains an entry
  id not seen before.
- `poll_interval_sec` (default 300 s) is enforced per feed URL; multiple
  tickets for the same URL are coalesced.

### Adding more platforms

The webhook server already detects a platform from its request headers,
so adding a new git forge that uses the GitHub-compatible HMAC scheme
requires no code — just set `platform` to a name in the payload detector
or extend `PLATFORM_VERIFIERS`. For a completely new transport (e.g.
Matrix, Telegram, websockets), subclass `NotificationProvider`,
implement `add_repository` / `start` / `stop`, then:

```python
from lucy_notes_manager.notifications import register_provider
register_provider("matrix", MatrixProvider)
```

After that, `"transport": "matrix"` works in your notifications config.
