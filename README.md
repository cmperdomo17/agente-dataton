# Agente Datatón

Agente conversacional que consulta datos de clientes, productos e inventario usando AWS (DynamoDB + Bedrock).

---

## Requisitos previos

- Python 3.10 o superior
- Una cuenta de AWS con acceso configurado (SSO o credenciales)
- Acceso a las tablas de DynamoDB y al modelo Claude 3.5 Haiku en Bedrock

---

## Instalación

1. **Clonar o descargar el proyecto**

```bash
cd "agente dataton"
```

2. **Instalar las dependencias**

```bash
pip install boto3 strands-agents python-dotenv
```

3. **Configurar variables de entorno** _(opcional)_

```bash
cp .env.example .env
# Editar .env con tu perfil AWS y preferencias
```

> Si no creas el `.env`, se usarán los valores por defecto definidos en `core/config.py`.

---

## Configuración de AWS

Antes de ejecutar el agente, hay que iniciar sesión en AWS con tu perfil de SSO.

Si aún no tienes un perfil configurado, créalo con:

```bash
aws configure sso
```

Luego inicia sesión:

```bash
aws sso login --profile TU_PERFIL
```

> Asegúrate de que el nombre del perfil coincida con el valor de `AWS_PROFILE` en tu `.env` o en `core/config.py`.

---

## Ejecución

```bash
python main.py
```

---

## Estructura del proyecto

```
agente dataton/
├── main.py                  # Punto de entrada — loop REPL interactivo
├── core/
│   ├── __init__.py
│   ├── config.py            # Configuración (variables de entorno con fallbacks)
│   ├── agent.py             # Fábrica del agente conversacional
│   ├── dynamo_service.py    # Consultas a DynamoDB con caché lazy
│   ├── prompt.py            # System prompt del agente (reglas y flujo)
│   └── data_dictionary.json # Diccionario de datos de las tablas
├── ui/
│   ├── __init__.py
│   └── console.py           # Interfaz visual de la consola
├── .env.example             # Template de variables de entorno
├── .gitignore
└── README.md
```

---

## Variables de entorno

| Variable            | Descripción                  | Default                                       |
| ------------------- | ---------------------------- | --------------------------------------------- |
| `AWS_PROFILE`       | Perfil SSO de AWS            | `Mario`                                       |
| `AWS_REGION`        | Región de AWS                | `us-east-2`                                   |
| `DYNAMO_PREFIX`     | Prefijo de tablas DynamoDB   | `omniretail_`                                 |
| `MAX_ROWS`          | Máximo de filas en respuesta | `20`                                          |
| `MODEL_ID`          | ID del modelo en Bedrock     | `us.anthropic.claude-3-5-haiku-20241022-v1:0` |
| `MODEL_TEMPERATURE` | Temperatura del modelo       | `0.0`                                         |
| `LOG_LEVEL`         | Nivel de logging             | `INFO`                                        |

---

## Notas

- Si la sesión de AWS expira, el agente dejará de funcionar. Solo vuelve a ejecutar `aws sso login --profile TU_PERFIL`.
- Los catálogos (productos, clientes, promociones) se cargan de DynamoDB la primera vez que se consultan y se mantienen en memoria durante toda la sesión.
