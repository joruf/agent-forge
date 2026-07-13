interface AgentRunningClockProps {
  className?: string;
  title?: string;
}

export function AgentRunningClock({ className = "", title }: AgentRunningClockProps) {
  return (
    <span
      className={`agent-running-clock-wrap ${className}`.trim()}
      title={title}
      aria-hidden={title ? undefined : true}
      aria-label={title}
    >
      <svg className="agent-running-clock" viewBox="0 0 24 24" focusable="false">
        <circle
          cx="12"
          cy="12"
          r="8.5"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
        />
        <g className="agent-running-clock-hour-hand">
          <line
            x1="12"
            y1="12"
            x2="12"
            y2="7"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
          />
        </g>
        <g className="agent-running-clock-minute-hand">
          <line
            x1="12"
            y1="12"
            x2="16.5"
            y2="12"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
          />
        </g>
        <circle cx="12" cy="12" r="1.3" fill="currentColor" />
      </svg>
    </span>
  );
}
