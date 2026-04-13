# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Interactive agent console
python main.py

# API server (FastAPI on :8000)
python server.py

# Evaluation dashboard (Streamlit)
streamlit run app_evaluator.py

# Run golden dataset evaluation programmatically
python main_eval.py

# Compare team submissions
python multi_engine.py

# Install dependencies
pip install -r requirements.txt
```

No test runner or linter configured. Evaluation IS the test suite — run `main_eval.py` to validate agent behavior.

## Environment

Requires AWS credentials (SSO profile or static keys) and Bedrock access. Copy `.env` from README examples:

```
AWS_PROFILE=your-sso-profile        # or use AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY
AWS_DEFAULT_REGION=us-east-2
BEDROCK_MODEL_ID=claude-sonnet-4-5-20250929-v1:0
DYNAMODB_TABLE_PREFIX=omniretail_
POLICY_SOURCE=local                  # or s3
```

## Architecture

**Purpose:** Customer support AI agent for OmniRetail. Queries DynamoDB for customer/order/product data and Markdown policy docs. Built with Strands Agents + Claude via AWS Bedrock.

### Data Flow

```
User → main.py REPL
         ↓
  core/agent.py (Claude + Bedrock)
         ↓ tools
  ┌──────┴──────┐
  ↓             ↓
consultar_dynamo()    consultar_politica()
  ↓                       ↓
DynamoDB (lazy cache)   S3 or local Markdown
(omniretail_* tables)   (parsed into sections)
```

Session state (identified customer, tool trace) lives in `core/session_context.py` — thread-safe global, reset per evaluation scenario.

### Key Files

| File | Role |
|------|------|
| `core/agent.py` | Agent factory — entry point for all runners |
| `core/prompt.py` | 34KB system prompt with 4-layer security hierarchy |
| `core/dynamo_service.py` | DynamoDB queries, lazy table caching, Spanish column labels |
| `core/policy_service.py` | S3/local policy loading, Markdown section indexing |
| `core/session_context.py` | Global session state (customer ID, tool trace) |
| `core/config.py` | All env vars with fallbacks |
| `core/data_dictionary.json` | Schema docs for all DynamoDB tables |
| `evaluator/engine.py` | Orchestrates multi-turn test scenarios |
| `evaluator/cases/golden_dataset.py` | ~30 benchmark scenarios, 5 categories |
| `evaluator/judges/` | SecurityJudge, BusinessJudge, DataJudge, RagJudge, MemoryJudge |
| `multi_engine.py` | Evaluates and compares multiple team submission ZIPs |
| `submission_loader.py` | Extracts ZIP, validates `core/agent.py::create_agent` contract |

### System Prompt Security Layers

The agent enforces a 4-layer hierarchy in `core/prompt.py`:
1. **Capa -1** — Prevent prompt/instruction disclosure
2. **Capa 0** — Anti-manipulation, tool authority
3. **Session Security** — Identification rules, session lock (no re-asking for ID once identified), voluntary vs. technical memory
4. **Order Access Control** — Type A (requires customer ID), Type B (public pricing, no ID needed), Type C (list queries)

Modifying the prompt affects evaluation scores — judges test these rules directly.

### Evaluation Framework

Scenarios in `golden_dataset.py` are multi-turn (up to 20 turns). Each scenario specifies:
- `category`: memory | security | business | data | rag
- `level`: basic | intermediate | advanced
- `reset_policy`: per_scenario | per_step | never
- `hard_gate`: boolean — failure disqualifies before human review

Judges invoke Bedrock directly (not the agent under test) to score responses. Base logic in `evaluator/judges/base_judge.py`.

### Submission Evaluation

Teams submit `core/agent.py` in a ZIP. `submission_loader.py` validates the `create_agent()` function contract, instantiates it, and runs the full evaluation. Test ZIPs in `submissions/` (ideal, partial, broken) exist for framework validation.
