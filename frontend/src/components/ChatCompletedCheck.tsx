interface ChatCompletedCheckProps {
  className?: string;
  title?: string;
}

export function ChatCompletedCheck({ className = "", title }: ChatCompletedCheckProps) {
  return (
    <span
      className={`chat-completed-check-wrap ${className}`.trim()}
      title={title}
      aria-hidden={title ? undefined : true}
      aria-label={title}
    >
      <svg className="chat-completed-check" viewBox="0 0 24 24" focusable="false">
        <circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" strokeWidth="2" />
        <path
          d="M8 12.2 10.6 14.8 16.2 9.2"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
    </span>
  );
}
