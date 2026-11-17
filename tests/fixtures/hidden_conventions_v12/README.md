# Released index fixture

`index.db` was built with the `v6.1.2` source and default settings. Its vault
contained `.conventions.md` (`Hidden policy token.\n`), `_conventions.md`
(`Legacy policy token.\n`), and `normal.md` (`# Ordinary\n\nRetained ordinary
token.\n`). The released default excluded only the legacy name. The database
therefore holds the hidden file and the normal note, with index semantics 12.
The boot reconciliation tests copy it into a temporary directory before use.
