import sys
import os

# Asegurar que encuentre la carpeta 'core'
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from core.agent import create_agent
from core.session_context import reset_session, get_tool_trace
from evaluator.cases.golden_dataset import GOLDEN_CASES
from evaluator.judges.security_judge import SecurityJudge
from evaluator.judges.business_judge import BusinessJudge

class EvaluationEngine:
    def __init__(self):
        # Registramos los jueces por categoría
        self.judges = {
            "security": SecurityJudge(),
            "business": BusinessJudge(),
            "sql": BusinessJudge(), # Se puede especializar más tarde
            "rag": BusinessJudge()
        }

    def run_all(self):
        print("🚀 Iniciando Evaluación Multi-Juez...")
        final_results = []

        for case in GOLDEN_CASES:
            print(f"\nEvaluating: {case['id']} - {case['name']}")
            reset_session()
            
            # Instanciamos el agente (el "alumno")
            agent = create_agent(streaming=False)
            
            # Ejecutar interacción
            response_obj = agent(case['user_input'])
            full_response = getattr(response_obj, 'content', str(response_obj))
            trace = get_tool_trace()

            # Seleccionar Juez según la categoría definida en el caso
            category = case.get("category", "business")
            judge = self.judges.get(category, self.judges['business'])
            
            # Obtener veredicto
            result = judge.evaluate(case['user_input'], full_response, trace)
            
            final_results.append({
                "id": case['id'],
                "score": result['score'],
                "feedback": result['feedback']
            })
            
            print(f"   [{category.upper()}] Score: {result['score']} | {result['feedback'][:70]}...")

        self._print_summary(final_results)

    def _print_summary(self, results):
        print("\n" + "="*60)
        print("📊 RESUMEN FINAL DE OMNIJUDGE")
        print("="*60)
        for r in results:
            emoji = "✅" if r['score'] >= 80 else "⚠️" if r['score'] >= 50 else "❌"
            print(f"{emoji} {r['id']}: {r['score']}/100 - {r['feedback']}")

if __name__ == "__main__":
    engine = EvaluationEngine()
    engine.run_all()