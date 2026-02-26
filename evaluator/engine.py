import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from core.agent import create_agent
from core.session_context import reset_session, get_tool_trace
from evaluator.cases.golden_dataset import GOLDEN_CASES
from evaluator.judges.security_judge import SecurityJudge
from evaluator.judges.business_judge import BusinessJudge
from evaluator.judges.data_judge import DataJudge

class EvaluationEngine:
    def __init__(self):
        self.judges = {
            "security": SecurityJudge(),
            "business": BusinessJudge(),
            "rag": BusinessJudge(),   # TODOideal: RagJudge
            "data": DataJudge()
        }

    def run_all(self):
        all_results = []

        for case in GOLDEN_CASES:
            trace = []
            full_response = ""
            try:
                reset_session() #TODO: casos cascadeados
                agent = create_agent(streaming=False)

                response_obj = agent(case["user_input"])
                full_response = getattr(response_obj, "content", str(response_obj))
                trace = get_tool_trace()

                judge = self.judges.get(case["category"], self.judges["business"])
                verdict = judge.evaluate(
                    case["user_input"],
                    full_response,
                    trace,
                    expected_data=case.get("expected_data")
                )

                all_results.append({
                    "id": case["id"],
                    "name": case["name"],
                    "category": case["category"],
                    "input": case["user_input"],
                    "response": full_response,
                    "score": int(verdict.get("score", 0)),
                    "feedback": verdict.get("feedback", "Sin feedback"),
                    "trace": trace,
                    "status": "ok"
                })

            except Exception as e:
                all_results.append({
                    "id": case.get("id", "UNKNOWN"),
                    "name": case.get("name", "Caso sin nombre"),
                    "category": case.get("category", "unknown"),
                    "input": case.get("user_input", ""),
                    "response": full_response,
                    "score": 0,
                    "feedback": f"Error en ejecución/evaluación: {str(e)}",
                    "trace": trace,
                    "status": "error"
                })

        return all_results