"""
Backend router — selects the active LLM backend at startup.

Set LLM_BACKEND=foundry in .env to use Microsoft Foundry Local.
Set LLM_BACKEND=anthropic (default) to use Anthropic Claude API.

Both backends expose the same interface used by agents and AgentManager:
  .complete()
  .complete_async()
  .stream()
  .stream_with_vision()
  .complete_with_reasoning()
  .complete_with_tools()
"""
import logging
from typing import TYPE_CHECKING

from config.settings import FOUNDRY_LOCAL_MODEL, FOUNDRY_LOCAL_URL, LLM_BACKEND

if TYPE_CHECKING:
    from core.foundry_interface import FoundryLocalInterface
    from core.llm_interface import LLMInterface

logger = logging.getLogger(__name__)


def get_llm_backend():
    """Return the active LLM interface based on LLM_BACKEND env var."""
    if LLM_BACKEND == "foundry":
        from core.foundry_interface import FoundryLocalInterface
        logger.info(
            "LLM backend: Microsoft Foundry Local  url=%s  model=%s",
            FOUNDRY_LOCAL_URL,
            FOUNDRY_LOCAL_MODEL,
        )
        iface = FoundryLocalInterface()
        health = iface.health_check()
        if health["status"] != "ok":
            logger.warning(
                "Foundry Local health check failed: %s — falling back to Anthropic",
                health.get("error"),
            )
            return _anthropic_backend()
        logger.info("Foundry Local models available: %s", health.get("models", []))
        return iface

    return _anthropic_backend()


def _anthropic_backend():
    from config.settings import ANTHROPIC_API_KEY
    from core.llm_interface import LLMInterface
    if not ANTHROPIC_API_KEY:
        logger.warning("ANTHROPIC_API_KEY is not set — LLM calls will fail")
    logger.info("LLM backend: Anthropic Claude API")
    return LLMInterface()


# Module-level singleton — import this everywhere instead of instantiating directly
_backend = None


def backend():
    global _backend
    if _backend is None:
        _backend = get_llm_backend()
    return _backend
