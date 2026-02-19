# Agente Datatón

Agente conversacional que consulta datos de clientes, productos e inventario usando AWS (DynamoDB y Athena).

---

## Requisitos previos

- Python 3.10 o superior
- Una cuenta de AWS con acceso configurado (SSO o credenciales)
- Acceso a las tablas de DynamoDB y al catálogo de Athena del proyecto

---

## Instalación

1. **Clonar o descargar el proyecto**

```bash
cd "agente dataton"
```

2. **Instalar las dependencias**

```bash
pip install boto3 openai python-dotenv
```

---

## Configuración de AWS

Antes de ejecutar el agente, hay que iniciar sesión en AWS con tu perfil de SSO.

Si aún no tienes un perfil configurado, créalo con:

```bash
aws configure sso
```

Luego inicia sesión con el nombre de perfil que hayas elegido:

```bash
aws sso login --profile TU_PERFIL
```

> Asegúrate de que el nombre del perfil coincida con el que está definido en `core/config.py`. Si es diferente, abre ese archivo y actualiza el valor del perfil.

---

## Ejecución

```bash
python main.py
```

---

## Estructura del proyecto

```
agente dataton/
├── main.py                  # Punto de entrada del agente
├── core/
│   ├── config.py            # Configuración general (perfil AWS, nombres de tablas, etc.)
│   ├── agent.py             # Lógica principal del agente conversacional
│   ├── dynamo_service.py    # Consultas a DynamoDB
│   └── athena_service.py    # Consultas a Athena
├── ui/
│   └── console.py           # Interfaz de consola para interactuar con el agente
└── README.md
```

---

## Notas

- Si la sesión de AWS expira, el agente dejará de funcionar. Solo vuelve a ejecutar `aws sso login --profile TU_PERFIL`.