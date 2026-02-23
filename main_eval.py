# main_eval.py
from core.agent import create_agent
from evaluator.engine import run_evaluation

if __name__ == "__main__":
    # Instanciamos el motor pasándole la función que crea al agente del "alumno"
    engine = run_evaluation(agent_factory=create_agent)
    
    # ¡Correr la evaluación!
    engine.run_all_tests()