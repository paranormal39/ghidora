from flask import Flask, request, jsonify, render_template, send_from_directory
import ollama
from datetime import datetime
import uuid
import discord
import asyncio
import threading
import os
from discord.ext import commands
import re
import json
from pathlib import Path
import subprocess
import shutil
import sqlite3
import time
import psutil
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

app = Flask(__name__)

# Role to LLM mapping for Ghidorah (three-headed dragon)
ROLE_LLM_MAPPING = {
    'code_generation': 'qwen2.5-coder:1.5b',
    'analysis': 'llama3.2:3b',
    'creative_writing': 'gemma2:2b',
    'problem_solving': 'llama3.2:3b',
    'research': 'gemma2:2b',
    'debugging': 'qwen2.5-coder:1.5b'
}

# Model display names
MODEL_DISPLAY_NAMES = {
    'llama3.2:3b': 'LLAMA 3.2',
    'qwen2.5-coder:1.5b': 'QWEN 2.5 CODER',
    'gemma2:2b': 'GEMMA2'
}

# Model colors for UI
MODEL_COLORS = {
    'llama3.2:3b': '#00ffff',  # Cyan
    'qwen2.5-coder:1.5b': '#ff0040',  # Red
    'gemma2:2b': '#ffff00'  # Yellow
}

# Conversation state storage
conversations = {}

# Discord bot configuration
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN', 'YOUR_BOT_TOKEN_HERE')
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)

# File editing configuration
CODE_EDITING_ENABLED = True
EDITABLE_EXTENSIONS = ['.py', '.js', '.html', '.css', '.md']
EDITED_FILES_LOG = []

# Git integration configuration
GIT_ENABLED = os.getenv('GIT_ENABLED', 'false').lower() == 'true'
GIT_REPO_PATH = os.getenv('GIT_REPO_PATH', os.getcwd())
AUTOMATION_COMMITS = []

# Conversation persistence
CONVERSATIONS_FILE = Path(os.getenv('CONVERSATIONS_FILE', 'conversations.json'))

# Pipeline configurations
PIPELINES = {
    'feature': ['analysis', 'code_generation', 'creative_writing'],
    'debug': ['analysis', 'debugging', 'analysis'],
    'review': ['analysis', 'code_generation', 'creative_writing'],
    'research': ['research', 'analysis', 'creative_writing']
}

# Head-to-head-to-head communication
HEAD_SEQUENCE = [
    ('llama3.2:3b', 'LLAMA 3.2', '#00ffff', 'Analysis Head'),
    ('qwen2.5-coder:1.5b', 'QWEN 2.5 CODER', '#ff0040', 'Code Head'),
    ('gemma2:2b', 'GEMMA2', '#ffff00', 'Creative Head')
]

# Scheduler configuration
SCHEDULER_ENABLED = os.getenv('SCHEDULER_ENABLED', 'false').lower() == 'true'
scheduler = BackgroundScheduler()
SCHEDULED_JOBS = {}

# Autonomous task queue
TASK_QUEUE = []
TASK_RESULTS = []
AUTONOMOUS_MODE = False
autonomous_thread = None

# File watcher configuration
FILE_WATCHER_ENABLED = os.getenv('FILE_WATCHER_ENABLED', 'false').lower() == 'true'
WATCH_DIRECTORY = os.getenv('WATCH_DIRECTORY', os.getcwd())
file_observer = None

# Safety settings
SAFE_MODE = os.getenv('SAFE_MODE', 'true').lower() == 'true'
ALLOW_FILE_WRITE = os.getenv('ALLOW_FILE_WRITE', 'false').lower() == 'true'
ALLOW_GIT_COMMIT = os.getenv('ALLOW_GIT_COMMIT', 'false').lower() == 'true'
ALLOW_TERMINAL = os.getenv('ALLOW_TERMINAL', 'false').lower() == 'true'

# Database configuration
DB_PATH = Path(os.getenv('DB_PATH', 'ghidora.db'))

# Benchmark storage (in-memory cache, persisted to SQLite)
BENCHMARK_RESULTS = []

class CodeFileHandler(FileSystemEventHandler):
    """Handle file system events for code files"""
    def __init__(self):
        self.last_modified = {}
    
    def on_modified(self, event):
        if event.is_directory:
            return
        
        file_path = event.src_path
        ext = Path(file_path).suffix
        
        if ext not in EDITABLE_EXTENSIONS:
            return
        
        # Debounce - ignore if modified within last 2 seconds
        now = datetime.now()
        if file_path in self.last_modified:
            if (now - self.last_modified[file_path]).total_seconds() < 2:
                return
        
        self.last_modified[file_path] = now
        print(f"📁 File modified: {file_path}")
        
        # Log the change
        EDITED_FILES_LOG.append({
            'timestamp': now.isoformat(),
            'file_path': file_path,
            'event': 'modified',
            'auto_detected': True
        })
        
        # Auto-commit if git is enabled
        if GIT_ENABLED:
            try:
                run_git_command(f'git add {file_path}', 'Auto-stage file')
                commit_msg = f"Auto-commit: Modified {Path(file_path).name}"
                run_git_command(f'git commit -m "{commit_msg}"', 'Auto-commit')
                AUTOMATION_COMMITS.append({
                    'timestamp': now.isoformat(),
                    'file': file_path,
                    'message': commit_msg
                })
                print(f"✅ Auto-committed: {commit_msg}")
            except Exception as e:
                print(f"❌ Auto-commit failed: {e}")

def start_file_watcher():
    """Start the file system watcher"""
    global file_observer
    if not FILE_WATCHER_ENABLED:
        return
    
    file_observer = Observer()
    handler = CodeFileHandler()
    file_observer.schedule(handler, WATCH_DIRECTORY, recursive=True)
    file_observer.start()
    print(f"👁️ File watcher started on: {WATCH_DIRECTORY}")

def stop_file_watcher():
    """Stop the file system watcher"""
    global file_observer
    if file_observer:
        file_observer.stop()
        file_observer.join()
        print("👁️ File watcher stopped")

def scheduled_health_check():
    """Scheduled job: Check health of all models"""
    print("⏰ Running scheduled health check...")
    models = ['llama3.2:3b', 'qwen2.5-coder:1.5b', 'gemma2:2b']
    for model in models:
        result = check_model_health(model)
        status = "✅" if result['status'] == 'healthy' else "❌"
        print(f"   {status} {model}: {result['status']}")

def scheduled_conversation_cleanup():
    """Scheduled job: Clean up old conversations"""
    print("⏰ Running conversation cleanup...")
    # Keep only last 100 conversations
    global conversations
    if len(conversations) > 100:
        sorted_convos = sorted(conversations.items(), key=lambda x: x[0])
        conversations = dict(sorted_convos[-100:])
        save_conversations()
        print(f"   Cleaned up, kept {len(conversations)} conversations")

def setup_scheduler():
    """Set up scheduled jobs"""
    if not SCHEDULER_ENABLED:
        return
    
    # Health check every 5 minutes
    scheduler.add_job(
        scheduled_health_check,
        IntervalTrigger(minutes=5),
        id='health_check',
        name='Health Check',
        replace_existing=True
    )
    SCHEDULED_JOBS['health_check'] = {'interval': '5 minutes', 'description': 'Check model health'}
    
    # Conversation cleanup at midnight
    scheduler.add_job(
        scheduled_conversation_cleanup,
        CronTrigger(hour=0, minute=0),
        id='conversation_cleanup',
        name='Conversation Cleanup',
        replace_existing=True
    )
    SCHEDULED_JOBS['conversation_cleanup'] = {'schedule': 'midnight', 'description': 'Clean old conversations'}
    
    scheduler.start()
    print("⏰ Scheduler started with jobs:", list(SCHEDULED_JOBS.keys()))

def init_database():
    """Initialize SQLite database with required tables"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS tasks (
            id TEXT PRIMARY KEY,
            type TEXT,
            prompt TEXT,
            output_file TEXT,
            status TEXT DEFAULT 'pending',
            created_at TEXT,
            started_at TEXT,
            completed_at TEXT
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS task_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id TEXT,
            final_output TEXT,
            file_written TEXT,
            file_error TEXT,
            error TEXT,
            created_at TEXT,
            FOREIGN KEY (task_id) REFERENCES tasks(id)
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS task_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id TEXT,
            step INTEGER,
            role TEXT,
            model TEXT,
            output TEXT,
            created_at TEXT,
            FOREIGN KEY (task_id) REFERENCES tasks(id)
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS model_benchmarks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            model TEXT,
            prompt TEXT,
            response_time REAL,
            response_length INTEGER,
            success INTEGER,
            error TEXT,
            cpu_before REAL,
            cpu_after REAL,
            ram_before REAL,
            ram_after REAL,
            timestamp TEXT
        )
    ''')
    
    conn.commit()
    conn.close()
    print(f"📦 Database initialized: {DB_PATH}")

def db_add_task(task):
    """Add a task to the database"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT OR REPLACE INTO tasks (id, type, prompt, output_file, status, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (
        task.get('id'),
        task.get('type', 'feature'),
        task.get('prompt', ''),
        task.get('output_file'),
        'pending',
        datetime.now().isoformat()
    ))
    conn.commit()
    conn.close()

def db_update_task_status(task_id, status, started_at=None, completed_at=None):
    """Update task status in database"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    if started_at:
        cursor.execute('UPDATE tasks SET status=?, started_at=? WHERE id=?', (status, started_at, task_id))
    elif completed_at:
        cursor.execute('UPDATE tasks SET status=?, completed_at=? WHERE id=?', (status, completed_at, task_id))
    else:
        cursor.execute('UPDATE tasks SET status=? WHERE id=?', (status, task_id))
    conn.commit()
    conn.close()

def db_add_task_result(task_id, result):
    """Add task result to database"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO task_results (task_id, final_output, file_written, file_error, error, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (
        task_id,
        result.get('final_output', ''),
        result.get('file_written'),
        result.get('file_error'),
        result.get('error'),
        datetime.now().isoformat()
    ))
    conn.commit()
    conn.close()

def db_add_task_log(task_id, step, role, model, output):
    """Add task step log to database"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO task_logs (task_id, step, role, model, output, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (task_id, step, role, model, output, datetime.now().isoformat()))
    conn.commit()
    conn.close()

def db_add_benchmark(benchmark):
    """Add benchmark result to database"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO model_benchmarks 
        (model, prompt, response_time, response_length, success, error, cpu_before, cpu_after, ram_before, ram_after, timestamp)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        benchmark['model'],
        benchmark['prompt'],
        benchmark['response_time'],
        benchmark['response_length'],
        1 if benchmark['success'] else 0,
        benchmark.get('error'),
        benchmark['cpu_before'],
        benchmark['cpu_after'],
        benchmark['ram_before'],
        benchmark['ram_after'],
        benchmark['timestamp']
    ))
    conn.commit()
    conn.close()

def db_get_recent_benchmarks(limit=50):
    """Get recent benchmark results from database"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM model_benchmarks ORDER BY id DESC LIMIT ?', (limit,))
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]

def db_get_recent_tasks(limit=50):
    """Get recent tasks from database"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM tasks ORDER BY created_at DESC LIMIT ?', (limit,))
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]

def load_conversations():
    """Load conversations from JSON file"""
    global conversations
    if CONVERSATIONS_FILE.exists():
        try:
            with open(CONVERSATIONS_FILE, 'r') as f:
                conversations = json.load(f)
        except Exception as e:
            print(f"Error loading conversations: {e}")
            conversations = {}

def save_conversations():
    """Save conversations to JSON file"""
    try:
        with open(CONVERSATIONS_FILE, 'w') as f:
            json.dump(conversations, f, indent=2, default=str)
    except Exception as e:
        print(f"Error saving conversations: {e}")

def get_system_stats():
    """Get current system statistics"""
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage('/')
    return {
        'cpu_percent': psutil.cpu_percent(interval=0.1),
        'ram_percent': mem.percent,
        'ram_used_gb': round(mem.used / (1024**3), 2),
        'ram_available_gb': round(mem.available / (1024**3), 2),
        'disk_percent': disk.percent,
        'current_time': datetime.now().isoformat()
    }

def check_model_health(model):
    """Check if a model is responsive"""
    try:
        res = ollama.chat(model=model, messages=[{'role': 'user', 'content': 'ping'}])
        return {'status': 'healthy', 'model': model}
    except Exception as e:
        return {'status': 'unhealthy', 'model': model, 'error': str(e)}

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint for all models"""
    models = ['llama3.2:3b', 'qwen2.5-coder:1.5b', 'gemma2:2b']
    results = {}
    all_healthy = True
    
    for model in models:
        result = check_model_health(model)
        results[model] = result
        if result['status'] != 'healthy':
            all_healthy = False
    
    return jsonify({
        'status': 'healthy' if all_healthy else 'degraded',
        'models': results,
        'git_enabled': GIT_ENABLED,
        'conversations_count': len(conversations),
        'timestamp': datetime.now().isoformat()
    }), 200 if all_healthy else 503

@app.route('/static/<path:filename>')
def static_files(filename):
    return send_from_directory('static', filename)

@app.route('/system', methods=['GET'])
def system_stats():
    """Get current system statistics"""
    stats = get_system_stats()
    stats['safe_mode'] = SAFE_MODE
    stats['allow_file_write'] = ALLOW_FILE_WRITE
    stats['allow_git_commit'] = ALLOW_GIT_COMMIT
    stats['allow_terminal'] = ALLOW_TERMINAL
    stats['autonomous_mode'] = AUTONOMOUS_MODE
    stats['task_queue_length'] = len(TASK_QUEUE)
    return jsonify(stats)

@app.route('/benchmark', methods=['POST'])
def run_benchmark():
    """Run a benchmark on a specific model"""
    data = request.get_json()
    model = data.get('model', 'llama3.2:3b')
    prompt = data.get('prompt', 'Write a hello world program in Python.')
    
    # Get system stats before
    cpu_before = psutil.cpu_percent(interval=0.1)
    ram_before = psutil.virtual_memory().percent
    
    benchmark = {
        'model': model,
        'prompt': prompt,
        'cpu_before': cpu_before,
        'ram_before': ram_before,
        'timestamp': datetime.now().isoformat()
    }
    
    try:
        start_time = time.time()
        res = ollama.chat(model=model, messages=[{'role': 'user', 'content': prompt}])
        end_time = time.time()
        
        response = res['message']['content']
        benchmark['response_time'] = round(end_time - start_time, 3)
        benchmark['response_length'] = len(response)
        benchmark['success'] = True
        benchmark['response'] = response[:500]  # First 500 chars for preview
        
    except Exception as e:
        benchmark['response_time'] = 0
        benchmark['response_length'] = 0
        benchmark['success'] = False
        benchmark['error'] = str(e)
    
    # Get system stats after
    benchmark['cpu_after'] = psutil.cpu_percent(interval=0.1)
    benchmark['ram_after'] = psutil.virtual_memory().percent
    
    # Save to database
    db_add_benchmark(benchmark)
    BENCHMARK_RESULTS.append(benchmark)
    
    return jsonify(benchmark)

@app.route('/benchmarks', methods=['GET'])
def get_benchmarks():
    """Get recent benchmark results"""
    limit = request.args.get('limit', 50, type=int)
    benchmarks = db_get_recent_benchmarks(limit)
    return jsonify({
        'benchmarks': benchmarks,
        'count': len(benchmarks)
    })

@app.route('/safety', methods=['GET'])
def get_safety_settings():
    """Get current safety settings"""
    return jsonify({
        'safe_mode': SAFE_MODE,
        'allow_file_write': ALLOW_FILE_WRITE,
        'allow_git_commit': ALLOW_GIT_COMMIT,
        'allow_terminal': ALLOW_TERMINAL
    })

@app.route('/roles', methods=['GET'])
def get_roles():
    return jsonify({
        'roles': [
            {'id': 'code_generation', 'name': 'Code Generation', 'llm': 'qwen2.5-coder:1.5b'},
            {'id': 'analysis', 'name': 'Analysis', 'llm': 'llama3.2:3b'},
            {'id': 'creative_writing', 'name': 'Creative Writing', 'llm': 'gemma2:2b'},
            {'id': 'problem_solving', 'name': 'Problem Solving', 'llm': 'llama3.2:3b'},
            {'id': 'research', 'name': 'Research', 'llm': 'gemma2:2b'},
            {'id': 'debugging', 'name': 'Debugging', 'llm': 'qwen2.5-coder:1.5b'},
            {'id': 'all_three', 'name': 'All Three Heads', 'llm': 'all'}
        ],
        'models': [
            {'id': 'llama3.2:3b', 'name': 'LLAMA 3.2', 'color': '#00ffff'},
            {'id': 'qwen2.5-coder:1.5b', 'name': 'QWEN 2.5 CODER', 'color': '#ff0040'},
            {'id': 'gemma2:2b', 'name': 'GEMMA2', 'color': '#ffff00'}
        ]
    })

@app.route('/chat', methods=['POST'])
def chat():
    print("--- Received Chat Request ---")
    data = request.get_json()
    user_prompt = data.get('prompt', '')
    role = data.get('role', 'analysis')
    conversation_id = data.get('conversation_id')
    start_time = datetime.now()

    if not conversation_id:
        conversation_id = str(uuid.uuid4())
        conversations[conversation_id] = []

    try:
        if role == 'all_three':
            # Get responses from all three models
            models = ['llama3.2:3b', 'qwen2.5-coder:1.5b', 'gemma2:2b']
            responses = {}

            for model in models:
                print(f"Asking {model}...")
                messages = [{'role': 'user', 'content': user_prompt}]
                if conversation_id in conversations:
                    messages = conversations[conversation_id] + messages

                res = ollama.chat(model=model, messages=messages)
                responses[model] = {
                    'response': res['message']['content'],
                    'model': model,
                    'display_name': MODEL_DISPLAY_NAMES[model],
                    'color': MODEL_COLORS[model]
                }
                print(f"{model} finished.")

            # Store all responses in conversation history
            for model, response_data in responses.items():
                conversations[conversation_id].extend([
                    {'role': 'user', 'content': f"[{MODEL_DISPLAY_NAMES[model]}] {user_prompt}"},
                    {'role': 'assistant', 'content': response_data['response'], 'model': model}
                ])

            end_time = datetime.now()
            processing_time = (end_time - start_time).total_seconds()

            save_conversations()
            return jsonify({
                'all_responses': responses,
                'role': role,
                'conversation_id': conversation_id,
                'timestamp': end_time.isoformat(),
                'processing_time': processing_time,
                'mode': 'all_three'
            })
        else:
            # Single model response
            model = ROLE_LLM_MAPPING.get(role, 'llama3.2:3b')
            print(f"Asking {model} for role: {role}")

            # Build conversation history
            messages = [{'role': 'user', 'content': user_prompt}]
            if conversation_id in conversations:
                messages = conversations[conversation_id] + messages

            res = ollama.chat(model=model, messages=messages)
            output = res['message']['content']

            # Store in conversation history
            conversations[conversation_id].extend([
                {'role': 'user', 'content': user_prompt},
                {'role': 'assistant', 'content': output, 'model': model}
            ])

            end_time = datetime.now()
            processing_time = (end_time - start_time).total_seconds()

            print(f"{model} finished.")

            save_conversations()
            return jsonify({
                'response': output,
                'model': model,
                'display_name': MODEL_DISPLAY_NAMES.get(model, model),
                'color': MODEL_COLORS.get(model, '#00ffff'),
                'role': role,
                'conversation_id': conversation_id,
                'timestamp': end_time.isoformat(),
                'processing_time': processing_time,
                'mode': 'single'
            })

    except Exception as e:
        print(f"ERROR: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/handoff', methods=['POST'])
def handoff():
    print("--- Received Handoff Request ---")
    data = request.get_json()
    conversation_id = data.get('conversation_id')
    current_role = data.get('current_role')
    task_summary = data.get('task_summary', '')

    if not conversation_id or conversation_id not in conversations:
        return jsonify({'error': 'Invalid conversation ID'}), 400

    # Determine the other LLM
    if current_role == 'code_generation':
        target_role = 'analysis'
        target_model = 'llama3.2:3b'
    else:
        target_role = 'code_generation'
        target_model = 'qwen2.5-coder:1.5b'

    try:
        # Create handoff prompt
        handoff_prompt = f"""The previous task was completed by the {current_role} model. Here's a summary:
{task_summary}

Please provide your suggestions, improvements, or additional insights on this task from your {target_role} perspective."""

        messages = conversations[conversation_id] + [
            {'role': 'user', 'content': handoff_prompt}
        ]

        print(f"Handing off to {target_model} for {target_role}")
        res = ollama.chat(model=target_model, messages=messages)
        output = res['message']['content']

        # Store handoff in conversation
        conversations[conversation_id].extend([
            {'role': 'user', 'content': f"[HANDOFF] {handoff_prompt}"},
            {'role': 'assistant', 'content': output}
        ])

        print(f"Handoff to {target_model} completed.")

        return jsonify({
            'response': output,
            'model': target_model,
            'role': target_role,
            'conversation_id': conversation_id,
            'timestamp': datetime.now().isoformat(),
            'handoff_from': current_role
        })

    except Exception as e:
        print(f"ERROR in handoff: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/edits', methods=['GET'])
def get_edits():
    """Get recent file edits"""
    return jsonify({
        'edits': EDITED_FILES_LOG[-20:],  # Return last 20 edits
        'total_edits': len(EDITED_FILES_LOG),
        'auto_editing_enabled': CODE_EDITING_ENABLED
    })

@app.route('/toggle-editing', methods=['POST'])
def toggle_editing_endpoint():
    """Toggle automatic code editing"""
    global CODE_EDITING_ENABLED
    CODE_EDITING_ENABLED = not CODE_EDITING_ENABLED
    
    return jsonify({
        'auto_editing_enabled': CODE_EDITING_ENABLED,
        'message': f"Automatic code editing {'enabled' if CODE_EDITING_ENABLED else 'disabled'}"
    })

@app.route('/implement', methods=['POST'])
def manual_implement_endpoint():
    """Manually implement a feature suggestion"""
    data = request.get_json()
    suggestion = data.get('suggestion', '')
    
    if not suggestion:
        return jsonify({'error': 'No suggestion provided'}), 400
    
    try:
        file_path, result = implement_feature_suggestion(suggestion)
        
        if file_path:
            return jsonify({
                'success': True,
                'file_path': file_path,
                'result': result
            })
        else:
            return jsonify({
                'success': False,
                'error': result
            })
            
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/git/status', methods=['GET'])
def git_status():
    """Get Git repository status"""
    if not GIT_ENABLED:
        return jsonify({'error': 'Git integration disabled'}), 400
    
    try:
        status_result = run_git_command('git status --porcelain', 'Get Git status')
        log_result = run_git_command('git log --oneline -10', 'Get recent commits')
        
        return jsonify({
            'git_enabled': True,
            'repo_path': GIT_REPO_PATH,
            'status': status_result,
            'recent_commits': log_result,
            'automation_commits': AUTOMATION_COMMITS[-5:]  # Last 5 automation commits
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/git/commit', methods=['POST'])
def git_commit_endpoint():
    """Manually trigger Git commit"""
    if not GIT_ENABLED:
        return jsonify({'error': 'Git integration disabled'}), 400
    
    data = request.get_json()
    message = data.get('message', 'Manual commit')
    
    try:
        result = run_git_command(f'git commit -m "{message}"', 'Manual commit')
        return jsonify({
            'success': True,
            'result': result,
            'message': message
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/history/<conversation_id>', methods=['GET'])
def get_history(conversation_id):
    if conversation_id not in conversations:
        return jsonify({'error': 'Conversation not found'}), 404

    return jsonify({
        'conversation_id': conversation_id,
        'history': conversations[conversation_id]
    })

@app.route('/pipeline/<pipeline_type>', methods=['POST'])
def run_pipeline(pipeline_type):
    """Run a multi-head pipeline (feature, debug, review, research)"""
    print(f"--- Received Pipeline Request: {pipeline_type} ---")
    
    if pipeline_type not in PIPELINES:
        return jsonify({'error': f'Unknown pipeline: {pipeline_type}. Available: {list(PIPELINES.keys())}'}), 400
    
    data = request.get_json()
    user_prompt = data.get('prompt', '')
    conversation_id = data.get('conversation_id')
    start_time = datetime.now()
    
    if not conversation_id:
        conversation_id = str(uuid.uuid4())
        conversations[conversation_id] = []
    
    pipeline_steps = PIPELINES[pipeline_type]
    pipeline_results = []
    current_context = user_prompt
    
    try:
        for i, role in enumerate(pipeline_steps):
            model = ROLE_LLM_MAPPING.get(role, 'llama3.2:3b')
            step_name = f"Step {i+1}: {role.replace('_', ' ').title()}"
            print(f"  {step_name} using {model}...")
            
            # Build prompt with context from previous steps
            if i == 0:
                step_prompt = current_context
            else:
                prev_response = pipeline_results[-1]['response']
                step_prompt = f"""Previous step output:
{prev_response}

Original request: {user_prompt}

Now, as the {role.replace('_', ' ')} specialist, please continue the work."""
            
            messages = [{'role': 'user', 'content': step_prompt}]
            res = ollama.chat(model=model, messages=messages)
            response = res['message']['content']
            
            pipeline_results.append({
                'step': i + 1,
                'role': role,
                'model': model,
                'display_name': MODEL_DISPLAY_NAMES.get(model, model),
                'color': MODEL_COLORS.get(model, '#00ffff'),
                'response': response
            })
            
            # Store in conversation
            conversations[conversation_id].extend([
                {'role': 'user', 'content': f"[PIPELINE:{pipeline_type}:{step_name}] {step_prompt[:200]}..."},
                {'role': 'assistant', 'content': response, 'model': model}
            ])
        
        end_time = datetime.now()
        processing_time = (end_time - start_time).total_seconds()
        
        save_conversations()
        return jsonify({
            'pipeline_type': pipeline_type,
            'steps': pipeline_results,
            'final_output': pipeline_results[-1]['response'] if pipeline_results else '',
            'conversation_id': conversation_id,
            'timestamp': end_time.isoformat(),
            'processing_time': processing_time,
            'mode': 'pipeline'
        })
        
    except Exception as e:
        print(f"ERROR in pipeline: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/pipelines', methods=['GET'])
def list_pipelines():
    """List available pipelines"""
    return jsonify({
        'pipelines': [
            {'id': 'feature', 'name': 'Feature Development', 'steps': PIPELINES['feature'], 'description': 'Analyze → Code → Document'},
            {'id': 'debug', 'name': 'Debug Workflow', 'steps': PIPELINES['debug'], 'description': 'Analyze → Debug → Verify'},
            {'id': 'review', 'name': 'Code Review', 'steps': PIPELINES['review'], 'description': 'Analyze → Suggest → Implement'},
            {'id': 'research', 'name': 'Research', 'steps': PIPELINES['research'], 'description': 'Research → Analyze → Summarize'}
        ]
    })

@app.route('/jobs', methods=['GET'])
def list_jobs():
    """List scheduled jobs"""
    jobs_info = []
    for job_id, info in SCHEDULED_JOBS.items():
        job = scheduler.get_job(job_id)
        jobs_info.append({
            'id': job_id,
            'info': info,
            'next_run': str(job.next_run_time) if job else None,
            'active': job is not None
        })
    
    return jsonify({
        'scheduler_enabled': SCHEDULER_ENABLED,
        'jobs': jobs_info
    })

@app.route('/jobs/<job_id>/trigger', methods=['POST'])
def trigger_job(job_id):
    """Manually trigger a scheduled job"""
    if job_id == 'health_check':
        scheduled_health_check()
        return jsonify({'success': True, 'message': 'Health check triggered'})
    elif job_id == 'conversation_cleanup':
        scheduled_conversation_cleanup()
        return jsonify({'success': True, 'message': 'Conversation cleanup triggered'})
    else:
        return jsonify({'error': f'Unknown job: {job_id}'}), 404

@app.route('/watcher/status', methods=['GET'])
def watcher_status():
    """Get file watcher status"""
    return jsonify({
        'enabled': FILE_WATCHER_ENABLED,
        'watch_directory': WATCH_DIRECTORY,
        'running': file_observer is not None and file_observer.is_alive() if file_observer else False,
        'recent_changes': EDITED_FILES_LOG[-10:]
    })

@app.route('/watcher/toggle', methods=['POST'])
def toggle_watcher():
    """Toggle file watcher on/off"""
    global FILE_WATCHER_ENABLED
    
    if file_observer and file_observer.is_alive():
        stop_file_watcher()
        return jsonify({'enabled': False, 'message': 'File watcher stopped'})
    else:
        FILE_WATCHER_ENABLED = True
        start_file_watcher()
        return jsonify({'enabled': True, 'message': f'File watcher started on {WATCH_DIRECTORY}'})

def process_autonomous_task(task):
    """Process a single task autonomously using the appropriate pipeline"""
    task_id = task.get('id', str(uuid.uuid4()))
    task_type = task.get('type', 'feature')  # feature, debug, review, research, or custom
    prompt = task.get('prompt', '')
    output_file = task.get('output_file')
    
    print(f"🤖 Processing task {task_id}: {prompt[:50]}...")
    
    # Update task status in database
    db_update_task_status(task_id, 'processing', started_at=datetime.now().isoformat())
    
    result = {
        'task_id': task_id,
        'task': task,
        'status': 'processing',
        'started_at': datetime.now().isoformat(),
        'steps': []
    }
    
    try:
        # Determine pipeline based on task type
        if task_type in PIPELINES:
            pipeline_steps = PIPELINES[task_type]
        else:
            # Default: use all three heads
            pipeline_steps = ['analysis', 'code_generation', 'creative_writing']
        
        current_output = prompt
        
        for i, role in enumerate(pipeline_steps):
            model = ROLE_LLM_MAPPING.get(role, 'llama3.2:3b')
            step_name = f"Step {i+1}: {role.replace('_', ' ').title()}"
            print(f"   {step_name} using {model}...")
            
            if i == 0:
                step_prompt = current_output
            else:
                step_prompt = f"""Previous step output:
{current_output}

Original task: {prompt}

Continue as the {role.replace('_', ' ')} specialist."""
            
            res = ollama.chat(model=model, messages=[{'role': 'user', 'content': step_prompt}])
            current_output = res['message']['content']
            
            result['steps'].append({
                'step': i + 1,
                'role': role,
                'model': model,
                'output': current_output
            })
            
            # Log step to database
            db_add_task_log(task_id, i + 1, role, model, current_output)
        
        result['final_output'] = current_output
        result['status'] = 'completed'
        result['completed_at'] = datetime.now().isoformat()
        
        # Write to file if specified AND allowed by safety settings
        if output_file:
            if SAFE_MODE and not ALLOW_FILE_WRITE:
                result['file_skipped'] = 'File write disabled by safety settings'
                print(f"   ⚠️ File write skipped (SAFE_MODE=true, ALLOW_FILE_WRITE=false)")
            else:
                try:
                    output_path = Path(output_file)
                    output_path.parent.mkdir(parents=True, exist_ok=True)
                    
                    # Extract code if present
                    code_content = current_output
                    if '```' in code_content:
                        code_blocks = re.findall(r'```[\w]*\n([\s\S]*?)```', code_content)
                        if code_blocks:
                            code_content = '\n\n'.join(code_blocks)
                    
                    with open(output_path, 'w', encoding='utf-8') as f:
                        f.write(code_content)
                    
                    result['file_written'] = str(output_path)
                    print(f"   ✅ Wrote output to {output_path}")
                    
                    EDITED_FILES_LOG.append({
                        'timestamp': datetime.now().isoformat(),
                        'file_path': str(output_path),
                        'task_id': task_id,
                        'auto_generated': True
                    })
                except Exception as e:
                    result['file_error'] = str(e)
                    print(f"   ❌ Failed to write file: {e}")
        
        # Update database
        db_update_task_status(task_id, 'completed', completed_at=datetime.now().isoformat())
        db_add_task_result(task_id, result)
        
        print(f"✅ Task {task_id} completed")
        
    except Exception as e:
        result['status'] = 'failed'
        result['error'] = str(e)
        result['completed_at'] = datetime.now().isoformat()
        db_update_task_status(task_id, 'failed', completed_at=datetime.now().isoformat())
        db_add_task_result(task_id, result)
        print(f"❌ Task {task_id} failed: {e}")
    
    return result

def run_autonomous_queue():
    """Process tasks in the queue autonomously"""
    global AUTONOMOUS_MODE, TASK_QUEUE, TASK_RESULTS
    
    print("🤖 Autonomous mode started - processing task queue...")
    
    while AUTONOMOUS_MODE and TASK_QUEUE:
        task = TASK_QUEUE.pop(0)
        result = process_autonomous_task(task)
        TASK_RESULTS.append(result)
        
        # Small delay between tasks
        import time
        time.sleep(1)
    
    AUTONOMOUS_MODE = False
    print("🤖 Autonomous mode finished - queue empty or stopped")

@app.route('/tasks', methods=['GET'])
def get_tasks():
    """Get current task queue and results"""
    recent_tasks = db_get_recent_tasks(20)
    return jsonify({
        'autonomous_mode': AUTONOMOUS_MODE,
        'queue': TASK_QUEUE,
        'queue_length': len(TASK_QUEUE),
        'results': TASK_RESULTS[-20:],  # Last 20 in-memory results
        'recent_tasks': recent_tasks,  # From database
        'total_completed': len(TASK_RESULTS),
        'safety': {
            'safe_mode': SAFE_MODE,
            'allow_file_write': ALLOW_FILE_WRITE,
            'allow_git_commit': ALLOW_GIT_COMMIT
        }
    })

@app.route('/tasks', methods=['POST'])
def add_tasks():
    """Add tasks to the queue"""
    data = request.get_json()
    tasks = data.get('tasks', [])
    
    if isinstance(tasks, dict):
        tasks = [tasks]  # Single task
    
    for task in tasks:
        if 'id' not in task:
            task['id'] = str(uuid.uuid4())
        TASK_QUEUE.append(task)
        db_add_task(task)  # Persist to database
    
    return jsonify({
        'added': len(tasks),
        'queue_length': len(TASK_QUEUE),
        'message': f'Added {len(tasks)} task(s) to queue'
    })

@app.route('/tasks/start', methods=['POST'])
def start_autonomous():
    """Start autonomous task processing"""
    global AUTONOMOUS_MODE, autonomous_thread
    
    if AUTONOMOUS_MODE:
        return jsonify({'error': 'Autonomous mode already running'}), 400
    
    if not TASK_QUEUE:
        return jsonify({'error': 'Task queue is empty'}), 400
    
    AUTONOMOUS_MODE = True
    autonomous_thread = threading.Thread(target=run_autonomous_queue, daemon=True)
    autonomous_thread.start()
    
    return jsonify({
        'status': 'started',
        'queue_length': len(TASK_QUEUE),
        'message': 'Autonomous processing started'
    })

@app.route('/tasks/stop', methods=['POST'])
def stop_autonomous():
    """Stop autonomous task processing"""
    global AUTONOMOUS_MODE
    
    AUTONOMOUS_MODE = False
    return jsonify({
        'status': 'stopping',
        'message': 'Autonomous mode will stop after current task'
    })

@app.route('/tasks/clear', methods=['POST'])
def clear_tasks():
    """Clear the task queue"""
    global TASK_QUEUE
    cleared = len(TASK_QUEUE)
    TASK_QUEUE = []
    return jsonify({
        'cleared': cleared,
        'message': f'Cleared {cleared} task(s) from queue'
    })

@app.route('/build', methods=['POST'])
def build_project():
    """Quick endpoint to build something - adds tasks and starts autonomous mode"""
    global AUTONOMOUS_MODE, autonomous_thread
    
    data = request.get_json()
    project_name = data.get('name', 'project')
    description = data.get('description', '')
    output_dir = data.get('output_dir', f'./generated/{project_name}')
    
    # Create a task list for building the project
    tasks = [
        {
            'id': f'{project_name}-plan',
            'type': 'research',
            'prompt': f"""Plan a small project: {description}

Create a detailed plan including:
1. Project structure (files needed)
2. Key features to implement
3. Dependencies required
4. Step-by-step implementation order

Keep it simple and focused."""
        },
        {
            'id': f'{project_name}-main',
            'type': 'feature',
            'prompt': f"""Based on this project plan, create the main implementation file.

Project: {description}

Create clean, working code with:
- Proper imports
- Main functionality
- Error handling
- Comments explaining key parts""",
            'output_file': f'{output_dir}/main.py'
        },
        {
            'id': f'{project_name}-readme',
            'type': 'creative_writing',
            'prompt': f"""Create a README.md for this project: {description}

Include:
- Project title and description
- Installation instructions
- Usage examples
- Features list""",
            'output_file': f'{output_dir}/README.md'
        }
    ]
    
    # Add tasks to queue
    for task in tasks:
        TASK_QUEUE.append(task)
    
    # Start autonomous processing
    if not AUTONOMOUS_MODE:
        AUTONOMOUS_MODE = True
        autonomous_thread = threading.Thread(target=run_autonomous_queue, daemon=True)
        autonomous_thread.start()
    
    return jsonify({
        'project': project_name,
        'tasks_queued': len(tasks),
        'output_dir': output_dir,
        'status': 'building',
        'message': f'Building {project_name} - {len(tasks)} tasks queued'
    })

@app.route('/automation', methods=['POST'])
def automation_endpoint():
    """Dedicated automation workflow endpoint"""
    print("--- Received Automation Request ---")
    data = request.get_json()
    user_prompt = data.get('prompt', '')
    conversation_id = data.get('conversation_id')
    start_time = datetime.now()
    
    if not conversation_id:
        conversation_id = str(uuid.uuid4())
    
    try:
        # Run automation workflow
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        result = loop.run_until_complete(automation_workflow(user_prompt, conversation_id))
        loop.close()
        
        end_time = datetime.now()
        processing_time = (end_time - start_time).total_seconds()
        
        if result.get('automation_complete'):
            return jsonify({
                'workflow_results': result['workflow_results'],
                'original_prompt': result['original_prompt'],
                'conversation_id': conversation_id,
                'timestamp': end_time.isoformat(),
                'processing_time': processing_time,
                'mode': 'automation'
            })
        else:
            return jsonify({'error': result.get('error', 'Automation failed')}), 500
            
    except Exception as e:
        print(f"ERROR in automation: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/head-to-head', methods=['POST'])
def head_to_head():
    """Web endpoint for head-to-head-to-head communication"""
    print("--- Received Head-to-Head Request ---")
    data = request.get_json()
    user_prompt = data.get('prompt', '')
    conversation_id = data.get('conversation_id')
    start_time = datetime.now()

    if not conversation_id:
        conversation_id = str(uuid.uuid4())

    try:
        # Run the async function in the event loop
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        result = loop.run_until_complete(head_to_head_response(user_prompt, conversation_id))
        loop.close()

        end_time = datetime.now()
        processing_time = (end_time - start_time).total_seconds()

        return jsonify({
            'head_responses': result['head_responses'],
            'final_response': result['final_response'],
            'original_prompt': result['original_prompt'],
            'conversation_id': conversation_id,
            'timestamp': end_time.isoformat(),
            'processing_time': processing_time,
            'mode': 'head_to_head'
        })

    except Exception as e:
        print(f"ERROR in head-to-head: {str(e)}")
        return jsonify({'error': str(e)}), 500

# Keep the old endpoint for backward compatibility
@app.route('/predict', methods=['POST'])
def predict():
    print("--- Received Request ---")
    data = request.get_json()
    user_prompt = data.get('prompt', 'Hello')

    try:
        print(f"Asking Llama 3.2...")
        res1 = ollama.chat(model='llama3.2:3b', messages=[{'role': 'user', 'content': user_prompt}])
        output1 = res1['message']['content']
        print("Llama finished.")

        print(f"Asking Qwen 2.5 Coder...")
        res2 = ollama.chat(model='qwen2.5-coder:1.5b', messages=[{'role': 'user', 'content': user_prompt}])
        output2 = res2['message']['content']
        print("Qwen finished.")

        return jsonify({
            'llama_output': output1,
            'qwen_output': output2
        })

    except Exception as e:
        print(f"ERROR: {str(e)}")
        return jsonify({'error': str(e)}), 500

def extract_file_path_from_suggestion(text):
    """Extract file path from feature suggestion"""
    text_lower = text.lower()
    
    # Look for file patterns like "file.py", "script.js", etc.
    file_patterns = [
        r'([\w\-\/\.]+\.(py|js|html|css|md))',
        r'"([\w\-\/\.]+\.(py|js|html|css|md))"',
        r'`([\w\-\/\.]+\.(py|js|html|css|md))`'
    ]
    
    for pattern in file_patterns:
        matches = re.findall(pattern, text_lower)
        if matches:
            return matches[0][0] if isinstance(matches[0], tuple) else matches[0]
    
    # Default to common files if no specific file mentioned
    if 'python' in text_lower or 'py' in text_lower:
        return 'main.py'
    elif 'javascript' in text_lower or 'js' in text_lower:
        return 'script.js'
    elif 'html' in text_lower:
        return 'index.html'
    elif 'css' in text_lower:
        return 'style.css'
    
    return None

def implement_feature_suggestion(suggestion_text, file_path=None):
    """Use Code Head to implement a feature suggestion"""
    try:
        if not file_path:
            file_path = extract_file_path_from_suggestion(suggestion_text)
        
        if not file_path:
            return None, "No specific file mentioned in the suggestion"
        
        # Check if file exists
        file_path = Path(file_path)
        if not file_path.exists():
            # Create new file
            file_path.touch()
            current_content = ""
        else:
            # Read existing content
            with open(file_path, 'r', encoding='utf-8') as f:
                current_content = f.read()
        
        # Create prompt for Code Head to implement the feature
        implementation_prompt = f"""You are the Code Head. The Creative Head suggested: "{suggestion_text}"

File: {file_path}
Current content:
```
{current_content}
```

Please implement this feature by providing the complete updated file content. Only respond with the code, no explanations.

If creating a new file, provide the complete initial code.
If modifying existing code, provide the complete updated code.

Make sure the code is syntactically correct and follows best practices."""
        
        # Get implementation from Code Head (synchronous call)
        res = ollama.chat(model='qwen2.5-coder:1.5b', messages=[{'role': 'user', 'content': implementation_prompt}])
        
        implemented_code = res['message']['content'].strip()
        
        # Clean up the response (remove markdown code blocks if present)
        if implemented_code.startswith('```'):
            lines = implemented_code.split('\n')
            if lines[0].startswith('```'):
                implemented_code = '\n'.join(lines[1:-1]) if lines[-1] == '```' else '\n'.join(lines[1:])
        
        # Write the implemented code to file
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(implemented_code)
        
        # Log the edit
        edit_log = {
            'timestamp': datetime.now().isoformat(),
            'file_path': str(file_path),
            'suggestion': suggestion_text,
            'implemented_by': 'QWEN 2.5 CODER (Code Head)',
            'file_created': not bool(current_content)
        }
        EDITED_FILES_LOG.append(edit_log)
        
        return str(file_path), f"✅ Successfully implemented feature in {file_path}"
        
    except Exception as e:
        return None, f"❌ Error implementing feature: {str(e)}"


def run_git_command(command, description):
    """Run a git command and return the output"""
    try:
        result = subprocess.run(
            command.split(),
            cwd=GIT_REPO_PATH,
            capture_output=True,
            text=True,
            timeout=30
        )
        return result.stdout.strip() if result.returncode == 0 else result.stderr.strip()
    except Exception as e:
        return f"Error: {str(e)}"


async def automation_workflow(user_prompt, conversation_id):
    """Run automation workflow with all three heads"""
    try:
        workflow_results = []
        
        for model, display_name, color, head_name in HEAD_SEQUENCE:
            loop = asyncio.get_event_loop()
            res = await loop.run_in_executor(
                None, 
                lambda m=model: ollama.chat(model=m, messages=[{'role': 'user', 'content': user_prompt}])
            )
            workflow_results.append({
                'response': res['message']['content'],
                'model': model,
                'display_name': display_name,
                'color': color,
                'head_name': head_name
            })
        
        return {
            'automation_complete': True,
            'workflow_results': workflow_results,
            'original_prompt': user_prompt
        }
    except Exception as e:
        return {'automation_complete': False, 'error': str(e)}


async def head_to_head_response(user_prompt, conversation_id):
    """Get responses from all three heads in sequence with context passing"""
    responses = []
    current_context = user_prompt
    
    for i, (model, display_name, color, head_name) in enumerate(HEAD_SEQUENCE):
        # Build messages with previous head's response
        messages = [{'role': 'user', 'content': current_context}]
        
        if i > 0:
            # Include previous head's response for context
            messages.append({
                'role': 'assistant', 
                'content': f"Previous head ({HEAD_SEQUENCE[i-1][3]}) response: {responses[-1]['response']}"
            })
            messages.append({
                'role': 'user', 
                'content': f"Now {head_name}, please respond to the original prompt considering the previous input: {user_prompt}"
            })
        
        # Get response from current head
        loop = asyncio.get_event_loop()
        res = await loop.run_in_executor(None, lambda: ollama.chat(model=model, messages=messages))
        response_text = res['message']['content']
        
        responses.append({
            'response': response_text,
            'model': model,
            'display_name': display_name,
            'color': color,
            'head_name': head_name
        })
    
    # Create final synthesized response
    final_prompt = f"""Based on the following responses from three AI heads, synthesize a final comprehensive answer:

Original question: {user_prompt}

"""
    for resp in responses:
        final_prompt += f"{resp['head_name']}: {resp['response']}\n\n"
    
    final_prompt += "Please provide a synthesized final response combining the best insights from all three heads."
    
    loop = asyncio.get_event_loop()
    final_res = await loop.run_in_executor(
        None, 
        lambda: ollama.chat(model='llama3.2:3b', messages=[{'role': 'user', 'content': final_prompt}])
    )
    
    return {
        'head_responses': responses,
        'final_response': final_res['message']['content'],
        'original_prompt': user_prompt
    }


@bot.event
async def on_ready():
    print(f'{bot.user.name} has connected to Discord!')
    print(f'Bot is in {len(bot.guilds)} servers')

@bot.command(name='ghidorah')
async def ghidorah(ctx, *, prompt: str):
    """Query all three heads for a synthesized response"""
    await ctx.send("🐉 **Ghidorah is awakening... all three heads are thinking.**")
    
    try:
        # This calls the helper function to get responses from all models
        result = await head_to_head_response(prompt, str(ctx.channel.id))
        
        # Create formatted response
        response_msg = "**🐉 GHIDORAH'S THREE HEADS RESPONSE**\n\n"
        
        for head_resp in result['head_responses']:
            response_msg += f"**{head_resp['head_name']} ({head_resp['display_name']})**:\n{head_resp['response']}\n\n"
        
        response_msg += f"**🔥 FINAL SYNTHESIZED RESPONSE**:\n{result['final_response']}"
        
        # Split if message is too long for Discord (2000 char limit)
        if len(response_msg) > 1900:
            chunks = [response_msg[i:i+1900] for i in range(0, len(response_msg), 1900)]
            for i, chunk in enumerate(chunks):
                await ctx.send(chunk if i == 0 else f"(continued)\n{chunk}")
        else:
            await ctx.send(response_msg)
            
    except Exception as e:
        await ctx.send(f"❌ Error: {str(e)}")

@bot.command(name='head')
async def single_head(ctx, head_name: str, *, prompt: str):
    """Query a specific head"""
    head_mapping = {
        'analysis': ('llama3.2:3b', 'LLAMA 3.2', 'Analysis Head'),
        'code': ('qwen2.5-coder:1.5b', 'QWEN 2.5 CODER', 'Code Head'),
        'creative': ('gemma2:2b', 'GEMMA2', 'Creative Head')
    }
    
    if head_name.lower() not in head_mapping:
        await ctx.send("Available heads: analysis, code, creative")
        return
    
    model, display_name, head_name_full = head_mapping[head_name.lower()]
    await ctx.send(f"🤔 **{head_name_full} is thinking...**")
    
    try:
        loop = asyncio.get_event_loop()
        res = await loop.run_in_executor(None, lambda: ollama.chat(model=model, messages=[{'role': 'user', 'content': prompt}]))
        
        response = f"**{head_name_full} ({display_name})**:\n{res['message']['content']}"
        await ctx.send(response)
    except Exception as e:
        await ctx.send(f"❌ Error: {str(e)}")


@bot.command(name='implement')
async def manual_implement(ctx, *, suggestion: str):
    """Manually implement a feature suggestion"""
    await ctx.send(f"🔧 **Code Head implementing feature...**\nSuggestion: {suggestion}")
    
    try:
        file_path, result = implement_feature_suggestion(suggestion)
        
        if file_path:
            await ctx.send(f"✅ **Successfully implemented in {file_path}**\n{result}")
        else:
            await ctx.send(f"❌ **Implementation failed**: {result}")
            
    except Exception as e:
        await ctx.send(f"❌ Error: {str(e)}")


def run_discord_bot():
    """Run Discord bot in separate thread"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    bot.run(DISCORD_TOKEN)

if __name__ == '__main__':
    # Initialize database
    init_database()
    
    # Load saved conversations
    load_conversations()
    print(f"📂 Loaded {len(conversations)} conversations from {CONVERSATIONS_FILE}")
    
    # Start scheduler
    setup_scheduler()
    
    # Start file watcher
    start_file_watcher()
    
    # Start Discord bot in background thread
    if DISCORD_TOKEN != 'YOUR_BOT_TOKEN_HERE':
        discord_thread = threading.Thread(target=run_discord_bot, daemon=True)
        discord_thread.start()
        print("🤖 Discord bot started in background thread")
    else:
        print("⚠️  Discord token not configured. Set DISCORD_TOKEN environment variable to enable Discord bot.")

    print("🐉 Ghidora Dashboard starting...")
    print(f"   Safe Mode: {'ON' if SAFE_MODE else 'OFF'}")
    print(f"   Allow File Write: {'Yes' if ALLOW_FILE_WRITE else 'No'}")
    print(f"   Allow Git Commit: {'Yes' if ALLOW_GIT_COMMIT else 'No'}")
    print(f"   Scheduler: {'Enabled' if SCHEDULER_ENABLED else 'Disabled'}")
    print(f"   File Watcher: {'Enabled' if FILE_WATCHER_ENABLED else 'Disabled'}")
    print(f"   Available Pipelines: {list(PIPELINES.keys())}")
    
    try:
        # Threaded=False can sometimes help debug local GPU collisions
        app.run(port=5000, debug=True)
    finally:
        # Cleanup on shutdown
        stop_file_watcher()
        if SCHEDULER_ENABLED:
            scheduler.shutdown()
