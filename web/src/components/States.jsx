export function LoadingState({ children = "Loading..." }) {
  return <p className="muted">{children}</p>;
}

export function ErrorState({ message }) {
  if (!message) return null;
  return <p className="error-message" style={{ padding: 0 }}>{message}</p>;
}

export function EmptyState({ children = "No records found." }) {
  return <p className="muted">{children}</p>;
}
