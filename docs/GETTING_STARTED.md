# First-run checklist

Install from the project root with Python 3.10+:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
tracecoder --version
tracecoder --demo
```

The demo uses a bundled scripted model and generated Fibonacci assertions. It needs no API key, Docker or pytest. It is a fixed host-execution demonstration; its success does not establish that a real model task or Docker sandbox works.

## Enable ordinary CLI Bash

Start Docker Desktop with its Linux Engine (on Windows/macOS), or start Docker Engine on Linux. Fetch the exact configured image once:

```powershell
docker info --format '{{.OSType}}'
docker pull ghcr.io/astral-sh/uv@sha256:aba5f865793af9275ceaa06d30935dfc6db8b81969c9fb099b0ebca6396b189e
docker image inspect ghcr.io/astral-sh/uv@sha256:aba5f865793af9275ceaa06d30935dfc6db8b81969c9fb099b0ebca6396b189e
```

The engine type must be `linux`, and image inspection must succeed. Pulling downloads the image; allow sufficient time and disk space. TraceCoder itself refuses to fetch or substitute an unverified image during execution. Its Bash commands run in a network-disabled container, so dependencies must already be available in the image or mounted workspace. `--yes` grants tool consent; it does not bypass isolation or workspace policy.

If Docker is stopped or the image is absent, file tools remain available but Bash returns an explicit unavailable result. Do not treat an unexecuted check as acceptance evidence.

## Configure a model

Set `TRACECODER_API_KEY`, `TRACECODER_BASE_URL` and `TRACECODER_MODEL` in your process or private `.env`. Never commit credentials. Start TraceCoder in the repository you intend it to modify. Ordinary chat and `/goal` are different: `/goal` enables the additional evidence gate.

For a one-shot mutating request, use `tracecoder --yes -p "YOUR_TASK"`. See the README for session recovery and the independent evaluation protocols for fixed-task checks.
