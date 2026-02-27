# AGENTS.md

## Purpose
This repository contains an evaluation framework for customer-support agents. The goal is to run a benchmark of golden cases against a real agent, capture telemetry from the execution, and score the result with specialized judges.

## Core architecture
- `evaluator/cases/golden_dataset.py`: benchmark definitions.
- `evaluator/engine.py`: orchestrates execution of the agent against every case.
- `core/session_context.py`: session state and telemetry layer.
- `evaluator/judges/base_judge.py`: shared Bedrock invocation and JSON parsing.
- `evaluator/judges/security_judge.py`: privacy and identity validation scoring.
- `evaluator/judges/business_judge.py`: protocol and customer service scoring.
- `evaluator/judges/data_judge.py`: data correctness, tool usage, and calculation scoring.
- `evaluator/judges/rag_judge.py` (planned): retrieval quality and document-groundedness scoring.

## Current evaluation flow
1. Load all golden cases.
2. For each case, reset session state.
3. Create the agent in non-streaming mode.
4. Execute the case input.
5. Read the final response and the tool trace.
6. Route the case to the correct judge by category.
7. Return a structured verdict with score, feedback, trace, and status.

## Key design principles
### 1. Separation of responsibilities
The engine does not know business policy details. Judges do not execute the agent. Session context does not score. This keeps the system modular.

### 2. Auditable evaluation
We do not evaluate only the final answer. We also inspect how the answer was produced through tool telemetry.

### 3. Category-specific judging
Security, business logic, data correctness, and document retrieval should not be judged with the same prompt or the same scoring logic.

### 4. Agent evaluability contract
Any agent submitted to the benchmark should expose an equivalent contract to the following:
- session reset capability
- session identity state
- tool call telemetry
- a way to export telemetry for evaluation

A submission can use a different internal implementation, but without this contract the benchmark cannot evaluate it reliably.

## Session context contract
`core/session_context.py` is the minimum reference implementation.

Required capabilities:
- `reset_session()` to clear identity and trace state
- `set_session_customer(customer_id, display_name=None)` to persist validated identity
- `get_session_customer_id()` to inspect whether identity has been established
- `add_tool_trace(tool, input_data, output_data)` to record auditable tool events
- `get_tool_trace()` to return a serializable trace for judges and reports

## Planned evolution
### 1. Scenario-based evaluation
Single-turn cases will evolve into multi-turn scenarios.
Each scenario should support:
- `reset_policy`: `always`, `per_scenario`, or `never`
- `steps`: ordered user messages
- step-level assertions and final scenario-level assertions

This is required to evaluate memory, identification followed by authorized lookup, and other cascading interactions.

### 2. Judge hardening
- `DataJudge` should combine deterministic checks with LLM judgment.
- `RagJudge` should be added to verify document-grounded answers, citation quality, and hallucination risk.
- `BusinessJudge` should use telemetry where relevant, not only final output.

### 3. Leveled golden datasets
The benchmark should be organized by:
- `basic`
- `intermediate`
- `advanced`

Basic cases act as hard gates. If an agent fails required basics, it should be disqualified before human review.

### 4. Latency and performance metrics
The evaluator should capture:
- TTFT: time to first token
- TRT: total response time
- end-to-end latency
- optional tool latency and per-step latency

## Suggested benchmark schema evolution
Recommended new case schema:

```python
{
  "id": "MEM-01",
  "name": "Memory - remembers provided name",
  "level": "basic",
  "category": "memory",
  "reset_policy": "per_scenario",
  "hard_gate": True,
  "steps": [
    {
      "user_input": "Hola, me llamo Juan",
      "judge": "memory",
      "expected": {"should_store_name": "Juan"}
    },
    {
      "user_input": "Como me llamo?",
      "judge": "memory",
      "expected": {"answer_contains": "Juan"}
    }
  ]
}
```

## Guidance for contributors
- Preserve the structured output contract: `score`, `feedback`, and status metadata.
- Prefer deterministic validation for hard rules, and LLM judges for semantic or nuanced checks.
- Do not hide tool usage from the evaluator. Telemetry is part of the benchmark contract.
- When adding a new judge, keep shared Bedrock logic in `BaseJudge` and place domain-specific reasoning in the judge itself.
- When adding new golden cases, specify whether they are hard gates and whether session reset should happen before each step or only before the scenario.

## Near-term implementation priorities
1. Add scenario support to the dataset and engine.
2. Add reset policies and multi-turn execution.
3. Improve `DataJudge` with deterministic checks.
4. Implement `RagJudge`.
5. Introduce leveled basic/intermediate/advanced suites.
6. Capture latency metrics in the engine.
