import { useState, useRef, useEffect } from "react";
import { sendChatMessage } from "../api.js";
import { renderMarkdown } from "../markdown.js";

function findPatient(toolCalls = []) {
  for (const call of toolCalls) {
    const found = call.result?.match(/Found:\s+(.+?)\s+\(patient_ref:\s*([^)]+)\)/);
    if (found) return { name: found[1], id: found[2] };
  }
  const callWithPatientRef = toolCalls.find((call) => call.arguments?.patient_ref);
  return callWithPatientRef ? { id: callWithPatientRef.arguments.patient_ref } : null;
}

function normalizePatientText(value) {
  return value.toLowerCase().replace(/\d+/g, "").replace(/[^a-z ]/g, " ").replace(/\s+/g, " ").trim();
}

function findCandidate(question, candidates) {
  const normalizedQuestion = normalizePatientText(question);
  return candidates.find((candidate) => {
    const normalizedName = normalizePatientText(candidate.name);
    return normalizedQuestion.includes(normalizedName);
  });
}

function findCandidates(toolCalls = []) {
  const candidates = [];
  const pattern = /-\s+(.+?)\s+\(patient_ref:\s*([^)]+)\)/g;
  for (const call of toolCalls) {
    for (const match of call.result?.matchAll(pattern) || []) {
      candidates.push({ name: match[1], id: match[2] });
    }
  }
  return candidates;
}

export default function ChatInterface() {
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [patient, setPatient] = useState(null);
  const [candidates, setCandidates] = useState([]);
  const [pendingQuestion, setPendingQuestion] = useState("");
  const bottomRef = useRef(null);
  const patientIdPattern = /\b(patient[_ -]?(id|ref)|mrn|medical record number|[0-9a-f]{16}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\b/i;

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  async function handleSend(rawQuestion = input) {
    const question = rawQuestion.trim();
    if (!question || sending) return;
    if (patientIdPattern.test(question)) {
      setMessages((prev) => [
        ...prev,
        { role: "user", content: question },
        { role: "assistant", content: "Patient IDs are not accepted in chat. Search by patient name instead.", isError: true },
      ]);
      setInput("");
      return;
    }

    const candidate = findCandidate(question, candidates);
    const activePatient = candidate || patient;
    const patientName = activePatient?.name || null;
    const apiQuestion = candidate ? pendingQuestion || question : question;

    if (candidate) {
      setPatient(candidate);
      setCandidates([]);
      setPendingQuestion("");
    }

    setMessages((prev) => [...prev, { role: "user", content: question }]);
    setInput("");
    setSending(true);

    try {
      const result = await sendChatMessage(apiQuestion, patientName);
      const nextPatient = findPatient(result.tool_calls);
      if (nextPatient) setPatient(nextPatient);
      const nextCandidates = findCandidates(result.tool_calls);
      setCandidates(nextCandidates);
      setPendingQuestion(nextCandidates.length ? apiQuestion : "");
      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: result.answer },
      ]);
    } catch (e) {
      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: `Error: ${e.message}`, isError: true },
      ]);
    } finally {
      setSending(false);
    }
  }

  const quickQuestions = patient
    ? ["Review readmission risk", "Summarize meds", "What follow-up is needed?"]
    : [];

  return (
    <div className="panel chat-panel">
      <div className="chat-header">
        <div>
          <h2>Patient chat</h2>
        </div>
        {patient && (
          <button className="patient-context" onClick={() => setPatient(null)} type="button">
            {patient.name || "Current patient"}
            <span>Patient ID masked</span>
          </button>
        )}
      </div>

      <div className="chat-container">
        <div className="chat-messages">
          {messages.length === 0 && (
            <div className="empty-chat">Start with a patient name, then ask follow-up questions.</div>
          )}
          {messages.map((m, i) => (
            <div key={i} className={`chat-message ${m.role}`}>
              <div className="bubble" style={m.isError ? { color: "var(--danger)" } : {}}>
                {m.role === "assistant" && !m.isError ? (
                  <div
                    className="chat-rendered"
                    dangerouslySetInnerHTML={{ __html: renderMarkdown(m.content) }}
                  />
                ) : (
                  m.content
                )}
              </div>
            </div>
          ))}
          {sending && <div className="muted thinking">Thinking...</div>}
          <div ref={bottomRef} />
        </div>

        {quickQuestions.length > 0 && (
          <div className="quick-questions">
            {quickQuestions.map((question) => (
              <button key={question} onClick={() => handleSend(question)} disabled={sending} type="button">
                {question}
              </button>
            ))}
          </div>
        )}

        <div className="chat-input-bar">
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleSend()}
            placeholder="Ask about a patient..."
            disabled={sending}
          />
          <button onClick={() => handleSend()} disabled={sending || !input.trim()}>
            Send
          </button>
        </div>
      </div>
    </div>
  );
}
