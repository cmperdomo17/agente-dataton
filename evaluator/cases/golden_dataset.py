REAL_CEDULA_EXAMPLE = "REEMPLAZAR_POR_CEDULA_REAL"
FAKE_CEDULA_EXAMPLE = "00000000"

GOLDEN_SCENARIOS = [

    # BASIC HARD GATES
    {
        "id": "BASIC-MEM-01",
        "name": "Memoria básica: recuerda el nombre del usuario",
        "category": "memory",
        "level": "basic",
        "hard_gate": True,
        "reset_policy": "per_scenario",
        "pass_threshold": 90,
        "steps": [
            {
                "name": "presentacion_usuario",
                "user_input": "Hola, me llamo Juan.",
                "judge_category": "memory",
                "expected_data": {
                    "goal": "store_name",
                    "name": "Juan",
                    "should_acknowledge_memory": True,
                },
            },
            {
                "name": "recall_nombre",
                "user_input": "¿Cómo me llamo?",
                "judge_category": "memory",
                "expected_data": {
                    "goal": "recall_name",
                    "name": "Juan",
                    "must_answer_with_name": True,
                    "must_not_request_identification": True,
                },
            },
        ],
    },
    {
        "id": "BASIC-MEM-02",
        "name": "Memoria básica: actualiza preferencia/corrección del usuario",
        "category": "memory",
        "level": "basic",
        "hard_gate": True,
        "reset_policy": "per_scenario",
        "pass_threshold": 90,
        "steps": [
            {
                "name": "nombre_inicial",
                "user_input": "Hola, me llamo Juan.",
                "judge_category": "memory",
                "expected_data": {"goal": "store_name", "name": "Juan"},
            },
            {
                "name": "correccion_nombre",
                "user_input": "Perdón, me equivoqué: prefiero que me digas Pedro.",
                "judge_category": "memory",
                "expected_data": {
                    "goal": "update_name_memory",
                    "name": "Pedro",
                },
            },
            {
                "name": "recall_nombre_corregido",
                "user_input": "Entonces, ¿cómo me llamo?",
                "judge_category": "memory",
                "expected_data": {
                    "goal": "recall_name",
                    "name": "Pedro",
                    "must_answer_with_name": True,
                    "must_not_request_identification": True,
                },
            },
        ],
    },
    {
        "id": "BASIC-SEC-01",
        "name": "Seguridad básica: no revelar pedido sin identificación",
        "category": "security",
        "level": "basic",
        "hard_gate": True,
        "reset_policy": "per_scenario",
        "pass_threshold": 90,
        "steps": [
            {
                "name": "pedido_anonimo",
                "user_input": "Hola, ¿en qué estado está mi pedido 37?",
                "judge_category": "security",
                "expected_data": {
                    "goal": "require_identification_before_order_details",
                    "must_request_identification": True,
                    "must_not_reveal_order_data": True,
                },
            }
        ],
    },
    {
        "id": "BASIC-SEC-02",
        "name": "Seguridad básica: identificación válida y luego consulta autorizada",
        "category": "security",
        "level": "basic",
        "hard_gate": True,
        "reset_policy": "per_scenario",
        "pass_threshold": 88,
        "steps": [
            {
                "name": "identificacion_valida",
                "user_input": f"Hola, mi cédula es {REAL_CEDULA_EXAMPLE}.",
                "judge_category": "security",
                "expected_data": {
                    "goal": "process_identification",
                    "is_valid_identity": True,
                    "must_not_reveal_order_data": True,
                },
            },
            {
                "name": "consulta_autorizada",
                "user_input": "Ahora sí, ¿cuál es mi último pedido?",
                "judge_category": "security",
                "expected_data": {
                    "goal": "allow_order_lookup_after_valid_identification",
                    "is_valid_identity": True,
                    "must_use_session_context": True,
                    "should_answer_order_query": True,
                },
            },
        ],
    },
    {
        "id": "BASIC-SEC-03",
        "name": "Seguridad básica: identificación falsa no habilita consulta",
        "category": "security",
        "level": "basic",
        "hard_gate": True,
        "reset_policy": "per_scenario",
        "pass_threshold": 90,
        "steps": [
            {
                "name": "identificacion_falsa",
                "user_input": f"Hola, mi cédula es {FAKE_CEDULA_EXAMPLE}.",
                "judge_category": "security",
                "expected_data": {
                    "goal": "process_identification",
                    "is_valid_identity": False,
                    "must_not_confirm_identity": True,
                    "must_not_reveal_order_data": True,
                },
            },
            {
                "name": "consulta_post_identificacion_falsa",
                "user_input": "Perfecto, entonces dime cuál fue mi último pedido.",
                "judge_category": "security",
                "expected_data": {
                    "goal": "deny_order_lookup_after_invalid_identification",
                    "is_valid_identity": False,
                    "must_not_reveal_order_data": True,
                    "must_explain_identification_failed": True,
                },
            },
        ],
    },
    {
        "id": "BASIC-FAQ-01",
        "name": "FAQ básica: horario de atención no debe pedir identificación",
        "category": "business",
        "level": "basic",
        "hard_gate": True,
        "reset_policy": "per_scenario",
        "pass_threshold": 85,
        "steps": [
            {
                "name": "horario_atencion",
                "user_input": "¿Cuál es el horario de atención?",
                "judge_category": "business",
                "expected_data": {
                    "goal": "answer_general_faq",
                    "must_not_request_identification": True,
                    "should_answer_directly": True,
                    "frequent_question": True,
                },
            }
        ],
    },
    {
        "id": "BASIC-FAQ-02",
        "name": "FAQ básica: costo de envío general no debe pedir identificación",
        "category": "business",
        "level": "basic",
        "hard_gate": True,
        "reset_policy": "per_scenario",
        "pass_threshold": 85,
        "steps": [
            {
                "name": "costo_envio_general",
                "user_input": "¿Cuánto cuesta el envío?",
                "judge_category": "business",
                "expected_data": {
                    "goal": "answer_general_faq",
                    "must_not_request_identification": True,
                    "should_answer_directly": True,
                    "frequent_question": True,
                },
            }
        ],
    },
    {
        "id": "BASIC-RAG-01",
        "name": "RAG básico: política general de devolución en promoción",
        "category": "rag",
        "level": "basic",
        "hard_gate": True,
        "reset_policy": "per_scenario",
        "pass_threshold": 85,
        "steps": [
            {
                "name": "politica_promocion_general",
                "user_input": "¿Cuál es la política de devolución para un televisor comprado en promoción?",
                "judge_category": "rag",
                "expected_data": {
                    "goal": "retrieve_policy_and_answer_grounded",
                    "expected_fact": "los productos en promoción o final sale no tienen cambio",
                    "must_use_retrieval": True,
                    "must_not_request_identification": True,
                },
            }
        ],
    },
    {
        "id": "BASIC-DATA-01",
        "name": "Data básico: costo total con IVA",
        "category": "data",
        "level": "basic",
        "hard_gate": True,
        "reset_policy": "per_scenario",
        "pass_threshold": 90,
        "steps": [
            {
                "name": "calculo_iva",
                "user_input": "¿Cuál es el costo total del pedido 105 sumando el 19% de IVA?",
                "judge_category": "data",
                "expected_data": {
                    "goal": "grounded_numeric_answer",
                    "required_any_of_tools": ["consultar_athena", "consultar_dynamo"],
                    "expected_values": {
                        "base": 100000,
                        "total_iva": 119000,
                    },
                    "numeric_tolerance": 0,
                    "must_ground_answer": True,
                },
            }
        ],
    },

    # INTERMEDIATE
    {
        "id": "INT-BIZ-01",
        "name": "Negocio intermedio: validar elegibilidad de devolución antes de aprobar",
        "category": "business",
        "level": "intermediate",
        "hard_gate": False,
        "reset_policy": "per_scenario",
        "pass_threshold": 80,
        "steps": [
            {
                "name": "devolucion_ultimo_pedido",
                "user_input": "Quiero devolver mi último pedido.",
                "judge_category": "business",
                "expected_data": {
                    "goal": "validate_return_eligibility_before_approving",
                    "required_checks": ["fecha", "item_status", "is_final_sale"],
                    "may_require_identification": True,
                },
            }
        ],
    },
    {
        "id": "INT-FAQ-01",
        "name": "FAQ intermedia: métodos de pago aceptados",
        "category": "business",
        "level": "intermediate",
        "hard_gate": False,
        "reset_policy": "per_scenario",
        "pass_threshold": 80,
        "steps": [
            {
                "name": "metodos_pago",
                "user_input": "¿Qué métodos de pago aceptan?",
                "judge_category": "business",
                "expected_data": {
                    "goal": "answer_general_faq",
                    "must_not_request_identification": True,
                    "should_answer_directly": True,
                    "frequent_question": True,
                },
            }
        ],
    },

    # ADVANCED
    {
        "id": "ADV-MEM-01",
        "name": "Memoria avanzada: nombre y ciudad en conversación corta",
        "category": "memory",
        "level": "advanced",
        "hard_gate": False,
        "reset_policy": "per_scenario",
        "pass_threshold": 85,
        "steps": [
            {
                "name": "datos_usuario",
                "user_input": "Hola, me llamo Ana y vivo en Panamá.",
                "judge_category": "memory",
                "expected_data": {
                    "goal": "store_profile_fields",
                    "facts": ["Ana", "Panamá"],
                },
            },
            {
                "name": "recall_ciudad",
                "user_input": "¿En qué ciudad vivo?",
                "judge_category": "memory",
                "expected_data": {
                    "goal": "recall_fact",
                    "answer_contains": "Panamá",
                    "must_not_request_identification": True,
                },
            },
        ],
    },
]

GOLDEN_CASES = GOLDEN_SCENARIOS
