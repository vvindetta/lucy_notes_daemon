# Lucy notes daemon (WIP)

Lucy daemon is modular notes-manager.

Your notes are just files. Use any editor you like. No editor plugins, accounts or clouds.

Lucy daemon monitors your note folder. Every time you edit something, it runs modules on that file (formatter, sync, git, etc).

Lucy can also read Unix-style flags written inside the note file and pass them to modules.

## How to use it
If `README.md` is one of your notes, you can write command flags directly inside it:

Send this note by email:

```--send vindetta@www.org```

Create a calendar event:

```--event 27.12 "need to finish writing readme"```

Then press ```CTRL+S``` - Lucy will detect the change and run the modules.

### Use cases  
- Auto-format files
- Sync notes between formats and programs
- Git auto-commit
- Calendar integration
- Rename notes based on rules
- Write your own module!

## How to sync with mobile?
Use Lucy’s git auto-commit module together with GitSync on Android:
https://github.com/ViscousPot/GitSync 

## Flags system
You can provide flags in three places:

1. Inside the note file (for per-note behavior)
2. In config.txt (global defaults)
3. At startup: python3 main.py --some-flag

Example config.txt:
```
--notes_dirs "/path/to/notes"
--todo-file "/path/to/todo.md"
--plasma-notes-dir "/path/to/plasma_notes"
--plasma-note-id "UUID"
```
