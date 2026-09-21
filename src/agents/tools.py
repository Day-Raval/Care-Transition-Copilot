"""
Tool schemas for the chat agent — exposes assess_readmission_risk() and
search_patient_chart() as functions an LLM can call via Groq's
OpenAI-compatible function-calling API.

This is deliberately SEPARATE from orchestrator.py's fixed pipeline.
The orchestrator is the right tool for "a discharge just happened,
produce an auditable draft plan" — deterministic, easy to test, the
same sequence every time. This chat agent is for a different, genuinely
more open-ended use case: a clinician asking an ad hoc question about a
patient, where which tool (if any) is needed depends on the actual
question. Both are legitimate; neither replaces the other.

search_patient_chart's query description was tightened after a real
failure: the model chose "discharge summary" as a free-text query for
an open-ended question, got a genuine "no relevant documentation found"
back, and then incorrectly told the user the chart lacked information
(medications) that Day-earlier testing already confirmed IS retrievable
with a specific query like "current medications at discharge". The
fix reuses the exact 5 category phrasings already validated throughout
this project (see retrieval_agent.py's RETRIEVAL_CATEGORIES) instead of
leaving query phrasing entirely to the model's judgment.
"""

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "assess_readmission_risk",
            "description": (
                "Runs the trained 30-day readmission risk model for a specific "
                "patient and returns their risk score, percentile, and category "
                "(low/medium/high). Use this when the user asks about a "
                "patient's risk level, whether they need follow-up, or how "
                "urgent their case is."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "patient_id": {"type": "string", "description": "The patient's unique ID"},
                },
                "required": ["patient_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_patient_chart",
            "description": (
                "Searches a specific patient's discharge notes for relevant "
                "clinical documentation. Use this when the user asks what's "
                "documented about a patient's medications, procedures, "
                "comorbidities, or any other chart content."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "patient_id": {"type": "string", "description": "The patient's unique ID"},
                    "query": {
                        "type": "string",
                        "description": (
                            "Use a SPECIFIC clinical category, not a vague term. "
                            "Known-good phrasings, confirmed to work well: "
                            "'current medications at discharge', "
                            "'chronic conditions and comorbidities in patient history', "
                            "'procedures conducted and treatment plan during this admission', "
                            "'chief complaint and reason for hospitalization', "
                            "'follow-up care instructions and discharge planning'. "
                            "Avoid generic terms like 'discharge summary' or "
                            "'patient overview' — they will not match well and can "
                            "return 'no relevant documentation found' even when the "
                            "information genuinely exists in the chart."
                        ),
                    },
                },
                "required": ["patient_id", "query"],
            },
        },
    },
]


def dispatch_tool_call(name: str, arguments: dict) -> str:
    """
    Executes the actual tool and returns a string result to feed back to
    the model. Every dispatch is a real call to the SAME functions the
    deterministic orchestrator uses (risk_tool.assess_risk,
    query_store.retrieve_relevant_context) — no separate, unaudited code
    path for the "agentic" version.
    """
    import sys
    sys.path.insert(0, ".")

    if name == "assess_readmission_risk":
        from src.agents.risk_tool import assess_risk
        result = assess_risk(arguments["patient_id"])
        return (
            f"Risk category: {result['risk_category']}, "
            f"percentile: {result['risk_percentile']:.0f}, "
            f"score: {result['risk_score']:.3f}, "
            f"admission reason: {result['admission_reason']}"
        )

    if name == "search_patient_chart":
        from src.retrieval.query_store import get_collection, retrieve_relevant_context
        collection = get_collection()
        results = retrieve_relevant_context(collection, arguments["patient_id"], arguments["query"])
        if results is None:
            return "No relevant documentation found for this query."
        return "\n".join(f"[{r['section']}] {r['text']}" for r in results)

    raise ValueError(f"Unknown tool: {name}")