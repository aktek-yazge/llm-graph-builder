"""
Vulture whitelist — symbols that appear dead but are used dynamically.

Vulture reads this file and treats all names defined here as "used".
Add entries when vulture reports false positives for:
  - FastAPI route handlers (called by framework, not user code)
  - Pydantic model fields (accessed by framework magic)
  - Celery task functions (called by broker, not user code)
  - LangChain / LangGraph tool functions (registered dynamically)
  - Click/Typer CLI commands
  - Test fixtures
"""

# --- FastAPI / Starlette route handlers ---
# These are registered via decorators and called by the ASGI framework.
get  # noqa
post  # noqa
put  # noqa
delete  # noqa
patch  # noqa
websocket  # noqa
lifespan  # noqa
on_event  # noqa
startup  # noqa
shutdown  # noqa

# --- Pydantic model fields & validators ---
# Field names and validators are used by Pydantic internals.
model_config  # noqa
model_validator  # noqa
field_validator  # noqa
model_post_init  # noqa
Config  # noqa

# --- Celery tasks ---
# Decorated with @celery_app.task / @shared_task — called by worker, not code.
bind  # noqa
name  # noqa
max_retries  # noqa
default_retry_delay  # noqa

# --- LangChain / LangGraph tool decorators ---
# Functions decorated with @tool are registered in a tool registry.
tool  # noqa
ToolNode  # noqa

# --- Common dynamic patterns ---
__all__  # noqa
__init__  # noqa
__str__  # noqa
__repr__  # noqa
__hash__  # noqa
__eq__  # noqa
