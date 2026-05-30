# 🚀 B2B Exception Engine

An autonomous, event-driven AI platform designed to dynamically triage, resolve, and audit supply chain exceptions in real-time. Built with a modern enterprise stack to handle high-throughput telemetry, the system replaces manual support workflows with intelligent AI agents capable of reasoning about inventory mismatch, pricing anomalies, and shipping logistics.

## 🌟 Key Features

- **Autonomous Agentic Triage**: Leverages Google Gemini 2.5 Flash to automatically reason about webhook errors and securely execute Python tooling (Warehouse Splitter, Tier Pricing Recalculator, Stock Inspector).
- **Event-Driven Architecture**: Fast asynchronous event bus powered by Redis Streams and Celery to process thousands of simultaneous webhooks without blocking the main event loop.
- **Fail-Safe Transactions**: Complete ACID compliance. The AI agent executes entirely within a strict PostgreSQL transactional sandbox. If the agent hallucinates, errors, or fails to resolve the issue, the database automatically rolls back all actions and safely flags the order for human review (`EXCEPTIONAL_HOLD`).
- **Enterprise Observability**: 100% telemetry tracing directly to LangSmith and an immutable, append-only `AgentAuditLog` database table for strict enterprise compliance and system transparency.
- **Beautiful Dashboard**: A blazing fast React and Tailwind UI that allows human operators to monitor 10,000+ orders, paginate seamlessly, and view beautifully formatted AI execution workflows.

---

## 🛠️ Technology Stack

- **AI/LLM**: Google Gemini 2.5 Flash, LangChain
- **Backend**: FastAPI, Python 3.9+, SQLAlchemy
- **Asynchronous Bus**: Redis Streams, Celery
- **Database**: PostgreSQL
- **Frontend**: React, Vite, Tailwind CSS v4, TypeScript
- **Testing**: Pytest (83-test comprehensive suite)

---

## 🚀 Getting Started

### Prerequisites
- Python 3.9 or higher
- Node.js (v18+)
- PostgreSQL (running on `localhost:5432`)
- Redis (running on `localhost:6379`)

### 1. Environment Setup
Clone the repository and set up your `.env` configuration file in the root directory:

```env
# ── PostgreSQL ──
POSTGRES_USER=your_user
POSTGRES_PASSWORD=your_password
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_DB=b2b_exceptions

# ── Redis & Celery ──
REDIS_HOST=localhost
REDIS_PORT=6379
REDIS_DB=0
CELERY_BROKER_URL=redis://localhost:6379/1
CELERY_RESULT_BACKEND=redis://localhost:6379/2

# ── Google Gemini API ──
GOOGLE_API_KEY=your_gemini_api_key

# ── LangSmith Observability ──
LANGCHAIN_TRACING_V2=true
LANGCHAIN_API_KEY=your_langsmith_api_key
LANGCHAIN_PROJECT=b2b-exception-engine
LANGCHAIN_ENDPOINT=https://api.smith.langchain.com
```

### 2. Install Dependencies
```bash
# Python Backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# React Frontend
cd frontend
npm install
cd ..
```

### 3. Run the Platform
To launch the entire platform (FastAPI, Celery, Redis Consumer, and React Dashboard), simply run the start script:

```bash
chmod +x start_local.sh
./start_local.sh
```
- **Dashboard**: `http://localhost:5173`
- **Backend API**: `http://localhost:8000`

---

## 🧠 Triggering AI Exceptions

You can manually fire error payloads to the webhook API to watch the AI jump into action! The payload must contain the `order_id` and an `error_code`.

```bash
curl -X POST "http://localhost:8000/api/webhooks/" \
-H "Content-Type: application/json" \
-d '{"order_id": 2, "error_code": "pricing_mismatch", "description": "Customer invoice tier does not match order total.", "source": "FinanceAPI", "metadata": {}}'
```

Head over to the React dashboard, click on the affected order, and watch the AI meticulously break down the problem, inspect the database, use a tool to recalculate the tier, and resolve the exception!

---

## 🧪 Testing
The engine comes fully validated with 83 comprehensive Pytest cases validating database deadlocks, webhook schemas, and AI deterministic outputs.
```bash
pytest -v
```

## 📜 License
MIT License
