# Guest sandbox server

Guest mode for chat.culture.dev puts visitors in an isolated **sandbox IRCd**
named `sbx`. It is a separate, unlinked culture server: guests never connect to
the real `spark` mesh server. AgentIRC has no join ACLs, ban/invite/key modes,
opers or flood control, so confinement cannot be done inside a shared IRCd;
the isolation boundary is a second server.

## Provisioning

```bash
culture server install --standalone --name sbx \
  --port 6700 --webhook-port 7700 --no-persist
```

`--standalone` writes a service unit for `culture-server-sbx` that runs:

```text
culture server start --foreground --name sbx --host 127.0.0.1 \
  --port 6700 --webhook-port 7700 --no-persist
```

Properties (spec obligation o3):

- **Loopback only.** `--host` defaults to `127.0.0.1` for `--standalone`. Check
  with `ss -ltn`: only `127.0.0.1:<port>` (and the loopback webhook port) are
  listed for the sandbox.
- **Never federates.** The unit has no `--link` and no `--mesh-config`, and
  `--standalone` does not read `mesh.yaml`. The server has no peers to
  handshake with, so its log shows no S2S handshake.
- **Own ports.** `--port` and `--webhook-port` are required so the sandbox can
  never collide with the spark server (6667 / 7680).
- **Nothing on disk (`--no-persist`).** The guest sandbox keeps channel
  history and rooms in memory only. AgentIRC commits history to its SQLite
  store only at shutdown, so a persisted sandbox would write a deleted
  guest's messages back to disk after the deletion. The durable record of
  guest chat is irc-lens's guest store, which the deletion flow fully covers.
  Without `--no-persist`, `--data-dir` defaults to `~/.culture/data-<name>`.

Remove the unit with `culture server uninstall --name sbx`. Starting it by
hand needs no service manager: run the `culture server start ...` line above.

## Where guest data lives

With `--no-persist` the sandbox server writes no guest content to disk at
all: history lives in its memory and is gone at restart. The durable record of
guest chat is irc-lens's guest store (questions and sbx-ask's answers), which
the guest deletion flow erases, keeping only anonymized Q&A. The spark data
directory (`~/.culture/data`) never holds guest content. Guest nicks on `sbx`
are prefixed `sbx-`, so `WHO` on the spark server never lists a sandbox nick.

## Verification

`tests/test_sandbox_server.py` starts a real `sbx` subprocess next to a real
spark IRCd on random ports and asserts: loopback-only listeners, no shared
channel traffic or NAMES entries, no accepted S2S handshake, and no outbound
connection from `sbx` to the spark port.
