import os


def env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


LLM_TIMEOUT_SECONDS = env_float("LLM_TIMEOUT_SECONDS", 45.0)
RISK_API_TIMEOUT_SECONDS = env_float("RISK_API_TIMEOUT_SECONDS", 10.0)
