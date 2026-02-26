# app_evaluator.py
import streamlit as st
import pandas as pd

st.set_page_config(page_title="OmniJudge Dashboard", layout="wide")

st.title("🛡️ OmniRetail: Auditoría de Agentes Inteligentes")
st.markdown("---")

st.sidebar.header("Configuración")

if st.sidebar.button("🚀 Ejecutar Evaluación"):
    try:
        from evaluator.engine import EvaluationEngine  # import lazy
        engine = EvaluationEngine()

        with st.spinner("Evaluando al agente..."):
            results = engine.run_all()
            df = pd.DataFrame(results)

        c1, c2, c3 = st.columns(3)
        avg_score = df['score'].mean()
        c1.metric("Score Promedio", f"{avg_score:.1f}%")
        c2.metric("Casos Aprobados", len(df[df['score'] >= 80]))
        c3.metric("Fallas de Seguridad", len(df[(df['category'] == 'security') & (df['score'] < 100)]))

        st.subheader("📋 Detalle de las Pruebas")
        st.dataframe(df[['id', 'name', 'category', 'score', 'feedback']], width='stretch')

        st.subheader("🔍 Inspección de Telemetría (Tool Trace)")
        for res in results:
            with st.expander(f"Caso {res['id']}: {res['name']} - {res['score']}/100"):
                col_a, col_b = st.columns(2)
                with col_a:
                    st.write("**Conversación:**")
                    st.chat_message("user").write(res['input'])
                    st.chat_message("assistant").write(res['response'])
                with col_b:
                    st.write("**Análisis del Juez:**")
                    st.warning(res['feedback'])
                    if res.get('trace'):
                        st.write("**Trazas Técnicas (SQL/Dynamo):**")
                        st.json(res['trace'])

    except Exception as e:
        st.error("Error ejecutando la evaluación")
        st.exception(e)

else:
    st.info("Presiona el botón en la barra lateral para iniciar la evaluación automática.")