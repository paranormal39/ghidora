# Ghidora - Local LLM Management Dashboard

A 3-headed local LLM management system that orchestrates multiple Ollama models for analysis, code generation, and creative tasks.

## What is Ghidora?

Ghidora is a Flask-based dashboard that manages three local LLM "heads":

| Head | Model | Purpose |
|------|-------|---------|
| 🔍 Analysis | Llama 3.2 (3B) | Problem solving, analysis, debugging |
| 💻 Code | Qwen 2.5 Coder (1.5B) | Code generation, implementation |
| ✨ Creative | Gemma2 (2B) | Creative writing, documentation, research |

**Features:**
- Web dashboard with real-time system monitoring
- Task pipelines (chain multiple heads together)
- Autonomous task queue with SQLite persistence
- Model benchmarking system
- Discord bot integration
- File watcher for auto-detection
- Scheduled jobs (health checks, cleanup)
- Safety settings to control autonomous actions

## Requirements

- Python 3.10+
- [Ollama](https://ollama.ai/) installed and running
- Required Ollama models (see below)

## Setup

### 1. Install Ollama Models

```bash
ollama pull llama3.2:3b
ollama pull qwen2.5-coder:1.5b
ollama pull gemma2:2b
```

### 2. Clone and Install

```bash
git clone https://github.com/paranormal39/ghidora.git
cd ghidora
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 3. Configure Environment

```bash
cp .env.example .env
# Edit .env with your settings
```

### 4. Run

```bash
python dashboard.py
```

Open http://localhost:5000

## Safety Settings

Ghidora includes safety controls for autonomous operations:

| Setting | Default | Description |
|---------|---------|-------------|
| `SAFE_MODE` | `true` | Master safety switch |
| `ALLOW_FILE_WRITE` | `false` | Allow autonomous file creation |
| `ALLOW_GIT_COMMIT` | `false` | Allow autonomous git commits |
| `ALLOW_TERMINAL` | `false` | Allow terminal command execution |

**Important:** With default settings, autonomous mode will process tasks but NOT write files or commit code. Enable these only when you trust the task queue.

## API Endpoints

### Core
- `GET /` - Dashboard UI
- `GET /health` - Model health check
- `GET /system` - System stats (CPU, RAM, disk)
- `GET /safety` - Current safety settings

### Chat
- `POST /chat` - Send message to a head
- `POST /head-to-head` - All heads collaborate
- `POST /pipeline/<type>` - Run a pipeline (feature, debug, review, research)

### Benchmarks
- `POST /benchmark` - Run a model benchmark
- `GET /benchmarks` - Get recent benchmark results

### Tasks (Autonomous)
- `GET /tasks` - View task queue and results
- `POST /tasks` - Add tasks to queue
- `POST /tasks/start` - Start autonomous processing
- `POST /tasks/stop` - Stop autonomous processing

### Automation
- `GET /jobs` - List scheduled jobs
- `POST /jobs/<id>/trigger` - Manually trigger a job
- `GET /watcher/status` - File watcher status

## Discord Bot Setup

1. Go to https://discord.com/developers/applications
2. Create a new application
3. Go to Bot tab → Add Bot → Copy token
4. Enable "Message Content Intent"
5. Add token to `.env`: `DISCORD_TOKEN=your_token_here`
6. Invite bot using OAuth2 URL generator (bot scope + send messages permission)

**Commands:**
- `!ask <prompt>` - Ask all three heads
- `!head <analysis|code|creative> <prompt>` - Ask specific head
- `!implement <suggestion>` - Implement a feature

## Database

Ghidora uses SQLite (`ghidora.db`) to persist:
- Tasks and task results
- Task execution logs
- Model benchmark results

Conversations are stored in `conversations.json`.

## Project Structure

```
ghidora/
├── dashboard.py      # Main application
├── requirements.txt  # Dependencies
├── .env.example      # Environment template
├── .env              # Your configuration (gitignored)
├── ghidora.db        # SQLite database (auto-created)
├── conversations.json # Chat history
└── templates/
    └── index.html    # Dashboard UI
```

## License

MIT
