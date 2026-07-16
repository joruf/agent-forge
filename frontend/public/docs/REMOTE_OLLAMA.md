# Remote Ollama Setup

AgentForge talks to Ollama over HTTP. The instance can run on your PC, in Docker, or on a NAS (e.g. Synology) on your LAN.

## Configuration

Set in `backend/.env`:

```env
AGENTFORGE_OLLAMA_BASE_URL=http://192.168.1.10:11434
AGENTFORGE_DEFAULT_MODEL=ollama/llama3.1:8b
AGENTFORGE_LLM_REQUEST_TIMEOUT=300
AGENTFORGE_MULTI_AGENT_MAX_ROUNDS_OLLAMA=2
```

| Variable | Purpose |
|----------|---------|
| `AGENTFORGE_OLLAMA_BASE_URL` | Base URL of the Ollama API (no trailing path) |
| `AGENTFORGE_DEFAULT_MODEL` | LiteLLM model id with `ollama/` prefix |
| `AGENTFORGE_LLM_REQUEST_TIMEOUT` | Increase for slow CPU-only or remote hosts (e.g. `600`) |
| `AGENTFORGE_MULTI_AGENT_MAX_ROUNDS_OLLAMA` | Fewer multi-agent rounds on Ollama (default `2`) |

Optional: pin one fast model for every task:

```env
AGENTFORGE_OVERRIDE_MODEL=ollama/llama3.2:1b-instruct-q4_K_M
```

## Verify connectivity

From the machine running AgentForge:

```bash
curl -s http://192.168.1.10:11434/api/tags
```

You should get JSON listing installed models. If this fails, fix network/firewall before starting AgentForge.

## Synology NAS notes

1. Install the **Ollama** package or run Ollama in Docker on the NAS.
2. Ensure port **11434** is reachable from your desktop (LAN firewall / DSM firewall rules).
3. Use the NAS LAN IP in `AGENTFORGE_OLLAMA_BASE_URL`, not `localhost` (that would point to the PC running AgentForge).
4. Pull models on the NAS: `ollama pull llama3.2:1b-instruct-q4_K_M` (via SSH or the NAS UI).
5. In AgentForge: **Settings → Models → Sync** or run the setup wizard to import tags.

## Performance tips

- Prefer smaller quantised models on CPU-only remote hosts (`1b` / `3b` instruct variants).
- Use **Single Agent** instead of Multi-Agent when latency is high.
- Increase `AGENTFORGE_LLM_REQUEST_TIMEOUT` if you see connection timeouts.
- Multi-agent on Ollama automatically uses fewer rounds (`AGENTFORGE_MULTI_AGENT_MAX_ROUNDS_OLLAMA`).

## Security

- Ollama has no built-in auth on the default HTTP API. Expose port 11434 only on trusted LANs or VPN.
- AgentForge backend binds to `127.0.0.1` by default; only the Ollama URL needs to reach the remote host.

## Troubleshooting

| Problem | Check |
|---------|--------|
| Connection refused | Ollama running? Correct IP/port? |
| Timeout | Increase timeout; use smaller model; reduce multi-agent rounds |
| Model not found | Pull model on Ollama host; sync in **Manage models** |
| Slow responses | Expected on NAS CPU; use override model or cloud provider for heavy tasks |

See also [README](../README.md) (LLM providers) and [TECHNICAL_DOCUMENTATION.md](TECHNICAL_DOCUMENTATION.md) (model routing).
