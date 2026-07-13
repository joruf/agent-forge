export const ALL_TASKS = [
  "coding",
  "code_review",
  "architecture",
  "research",
  "documentation",
  "coordination",
  "sql",
  "vision",
  "finance",
  "general",
  "title",
] as const;

export type TaskId = (typeof ALL_TASKS)[number];
