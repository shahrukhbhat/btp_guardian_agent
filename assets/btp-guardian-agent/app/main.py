import os

# CRITICAL: Joule telemetry block — runs only on Joule/Kyma (JOULE_RUNTIME=1).
# On Cloud Foundry (JOULE_RUNTIME unset), these imports are skipped — sap_cloud_sdk
# is not installed in CF requirements.txt and would cause ImportError at startup.
if os.environ.get("JOULE_RUNTIME"):
    from sap_cloud_sdk.aicore import set_aicore_config
    from sap_cloud_sdk.core.telemetry import auto_instrument
    set_aicore_config()
    auto_instrument()

import logging

from a2a.server.apps import A2AStarletteApplication
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import AgentCapabilities, AgentCard, AgentSkill

from agent_executor import AgentExecutor
from opentelemetry.instrumentation.starlette import StarletteInstrumentor

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "5000"))


def _build_app():
    skill = AgentSkill(
        id="btp-guardian-agent",
        name="btp-guardian-agent",
        description="An AI-powered conversational agent that gives SAP BTP platform teams and FinOps stakeholders instant, natural-language visibility into their cloud consumption, costs, account topology, entitlements, and governance posture.",
        tags=["btp", "guardian", "finops", "governance", "consumption"],
        examples=[
            "Which subaccounts have the highest cost this month?",
            "Show me all entitlements that are over 85% utilized",
            "Are there any governance policy violations in my BTP landscape?",
            "Give me a summary of my global account topology",
        ],
    )
    agent_card = AgentCard(
        name="btp-guardian-agent",
        description="An AI-powered conversational agent that gives SAP BTP platform teams and FinOps stakeholders instant, natural-language visibility into their cloud consumption, costs, account topology, entitlements, and governance posture.",
        url=os.environ.get("AGENT_PUBLIC_URL", f"http://{HOST}:{PORT}/"),
        version="1.0.0",
        default_input_modes=["text", "text/plain"],
        default_output_modes=["text", "text/plain"],
        capabilities=AgentCapabilities(streaming=True, push_notifications=False),
        skills=[skill],
    )
    server = A2AStarletteApplication(
        agent_card=agent_card,
        http_handler=DefaultRequestHandler(
            agent_executor=AgentExecutor(),
            task_store=InMemoryTaskStore(),
        ),
    )
    app = server.build()
    StarletteInstrumentor().instrument_app(app)
    return app


# `application` is the WSGI/ASGI entry point consumed by gunicorn on CF.
# `--chdir app main:application` in the start command resolves this module
# from inside app/, so flat peer imports (agent_executor, agent, etc.) work.
application = _build_app()


def main():
    """Local development entry point (uvicorn direct)."""
    import uvicorn
    logger.info("Starting A2A server at http://%s:%d", HOST, PORT)
    uvicorn.run(application, host=HOST, port=PORT)


if __name__ == "__main__":
    main()
