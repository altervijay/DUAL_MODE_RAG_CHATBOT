"""
Pydantic request schemas for the API.

SSE event payloads (`tool`, `token`, `citations`, `error`, `done`) are
plain dicts built in router.py/main.py — see router.run()'s docstring for
their shapes.
"""

from pydantic import BaseModel


class ChatRequest(BaseModel):
    message: str
