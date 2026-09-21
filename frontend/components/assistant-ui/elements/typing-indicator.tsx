/* Local typing-indicator element, styled after the assistant-ui
 * TypingIndicator design component (three dots in an incoming bubble).
 * Purely presentational: visibility is gated by `AssistantTyping` in
 * `@/components/aui-thread`, which reads the thread run state. */

const DOT_DELAYS = ["0ms", "150ms", "300ms"];

export function TypingIndicator() {
  return (
    <div role="status" aria-label="Assistant is typing" className="flex justify-start">
      <div className="rounded-lg border border-slate-200 bg-white px-3 py-2 shadow-sm">
        <span className="flex items-center gap-1" aria-hidden="true">
          {DOT_DELAYS.map((delay) => (
            <span
              key={delay}
              className="typing-dot inline-block h-1.5 w-1.5 rounded-full bg-slate-400"
              style={{ animationDelay: delay }}
            />
          ))}
        </span>
        <span className="sr-only">Assistant is typing</span>
      </div>
    </div>
  );
}
