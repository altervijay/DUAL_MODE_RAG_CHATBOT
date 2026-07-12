

import json
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sse_starlette.sse import EventSourceResponse

from app.agent import router
from app.db import init_db
from app.models import ChatRequest

logger = logging.getLogger(__name__)

app = FastAPI(title="Dual-Mode Agentic RAG Chatbot")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # no auth/cookies in play, so a wide-open origin is fine
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup() -> None:
    init_db()


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/chat")
async def chat(request: ChatRequest):
    async def event_stream():
        try:
            async for event_type, payload in router.run(request.message):
                yield {"event": event_type, "data": json.dumps(payload)}
        except Exception:
            # Full traceback server-side; short generic message to the client.
            logger.exception("chat stream failed")
            yield {
                "event": "error",
                "data": json.dumps(
                    {"message": "Something went wrong while generating the answer. Please try again."}
                ),
            }
        yield {"event": "done", "data": "{}"}

    return EventSourceResponse(event_stream())
