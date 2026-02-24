# Agente Datatón

Agente conversacional que consulta datos de clientes, productos e inventario usando AWS (DynamoDB + Bedrock), y responde preguntas sobre políticas de devoluciones, envíos y garantías.

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

## Configuración de Políticas (S3)

Los documentos de políticas (devoluciones, envíos, garantía) pueden consultarse de dos formas:

### Opción 1: Carpeta local (por defecto)

No requiere configuración. El agente lee los archivos `.md` de la carpeta `politicas/`.

### Opción 2: Amazon S3 (producción)

1. Crear un bucket S3 y subir los 3 archivos Markdown:

```bash
aws s3 cp politicas/ s3://TU_BUCKET/politicas/ --recursive
```

2. Configurar las variables en `.env`:

```env
POLICY_S3_BUCKET=tu-bucket-omniretail
POLICY_S3_PREFIX=politicas/
```

> **Costo estimado:** ~$0.01/mes (26 KB de almacenamiento + lecturas mínimas).

---

## Ejecución

```bash
python main.py
```

---

## Arquitectura

```
┌─────────────┐
│   Cliente    │
└──────┬──────┘
       │
┌──────▼──────┐
│   Agente    │  (Claude 3.5 Haiku via Bedrock)
│  OmniRetail │
└──┬───────┬──┘
   │       │
   │       │  consultar_politica()
   │       ▼
   │  ┌──────────────┐     ┌─────────────┐
   │  │Policy Service│────▶│ S3 / Local  │
   │  │ (caché mem.) │     │(políticas/) │
   │  └──────────────┘     └─────────────┘
   │
   │  consultar_dynamo()
   ▼
┌──────────────┐
│  DynamoDB    │
│(caché lazy)  │
└──────────────┘
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
│   ├── policy_service.py    # Consulta de políticas (S3 / local) con caché lazy
│   ├── prompt.py            # System prompt del agente (reglas y flujo)
│   └── data_dictionary.json # Diccionario de datos de las tablas
├── politicas/               # Documentos de políticas en Markdown
│   ├── Política de devoluciones.md
│   ├── Políticas de envío.md
│   └── Política de garantía.md
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
| `POLICY_S3_BUCKET`  | Bucket S3 para políticas     | _(vacío = usa carpeta local)_                 |
| `POLICY_S3_PREFIX`  | Prefijo dentro del bucket S3 | `politicas/`                                  |

---

## Notas

- Si la sesión de AWS expira, el agente dejará de funcionar. Solo vuelve a ejecutar `aws sso login --profile TU_PERFIL`.
- Los catálogos (productos, clientes, promociones) se cargan de DynamoDB la primera vez que se consultan y se mantienen en memoria durante toda la sesión.
- Los documentos de políticas se cargan y parsean en secciones al inicio. Las consultas posteriores son instantáneas (~0ms).
