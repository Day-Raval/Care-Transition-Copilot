import { useState, useRef, useEffect } from "react";
import { sendChatMessage } from "../api.js";

export default function ChatInterface() {
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const bottomRef = useRef(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  async function handleSend() {
    const question = input.trim();
    if (!question || sending) return;

    setMessages((prev) => [...prev, { role: "user", content: question }]);
    setInput("");
    setSending(true);

    try {
      const result = await sendChatMessage(question);
      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: result.answer, toolCalls: result.tool_calls },
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

  return (
    <div className="panel" style={{ maxWidth: 720 }}>
      <h2>Ask a question</h2>
      <p className="panel-subtitle">
        The assistant decides which tools it needs — risk assessment, chart search, or both.
        Every tool call is shown below the answer, so you can see exactly how the response was
        constructed. Include a patient ID in your question.
      </p>

      <div className="chat-container">
        <div className="chat-messages">
          {messages.length === 0 && (
            <p className="muted">
              Try: "What is Hai Marvin's readmission risk?" or "Should I be worried about patient
              &lt;id or name&gt;?"
            </p>
          )}
          {messages.map((m, i) => (
            <div key={i} className={`chat-message ${m.role}`}>
              {m.role === "assistant" && m.toolCalls?.length > 0 && (
                <div className="tool-call-log">
                  {m.toolCalls.map((tc, j) => (
                    <div key={j}>
                      <span className="tool-name">{tc.name}</span>({JSON.stringify(tc.arguments)})
                      <div style={{ marginTop: 2 }}>
                        &rarr; {tc.result.slice(0, 150)}{tc.result.length > 150 ? "…" : ""}
                      </div>
                    </div>
                  ))}
                </div>
              )}
              <div className="bubble" style={m.isError ? { color: "var(--danger)" } : {}}>
                {m.content}
              </div>
            </div>
          ))}
          {sending && <div className="muted" style={{ padding: 12 }}>Thinking…</div>}
          <div ref={bottomRef} />
        </div>

        <div className="chat-input-bar">
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleSend()}
            placeholder="Ask about a specific patient…"
            disabled={sending}
          />
          <button onClick={handleSend} disabled={sending || !input.trim()}>
            Send
          </button>
        </div>
      </div>
    </div>
  );
}