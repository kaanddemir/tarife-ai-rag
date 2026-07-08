import type { Message } from "@/lib/types";
import AnswerMarkdown from "./AnswerMarkdown";
import CitationSources from "./CitationSources";
import ComparisonTable from "./ComparisonTable";

export default function MessageBubble({ message }: { message: Message }) {
  const isUser = message.role === "user";
  const hasTable = !isUser && message.packages && message.packages.length > 0;

  return (
    <div className={`row ${message.role}`}>
      <div className={`bubble ${message.role}${hasTable ? " full" : ""}`}>
        <div className={message.error ? "error" : undefined}>
          {isUser
            ? message.content
            : <AnswerMarkdown text={message.content} citations={message.citations ?? []} />}
        </div>

        {hasTable && <ComparisonTable packages={message.packages!} />}

        {!isUser && (
          <CitationSources
            citations={message.citations ?? []}
            copyText={message.error ? undefined : message.content}
          />
        )}
      </div>
    </div>
  );
}
