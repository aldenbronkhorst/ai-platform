interface ChatEmptyStateProps {
  displayName: string;
}

export function ChatEmptyState({ displayName }: ChatEmptyStateProps) {
  const firstName = displayName.split(" ")[0];

  return (
    <div className="flex-1 flex flex-col items-center justify-center px-6 py-12 select-none">
      <div className="max-w-lg w-full text-center">
        <div className="space-y-2">
          <h2 className="text-2xl font-bold text-default">
            What would you like to work on, {firstName}?
          </h2>
          <p className="text-sm text-muted">
            Ask about business operations, run audits, or check connected systems.
          </p>
        </div>
      </div>
    </div>
  );
}
