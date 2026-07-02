interface ChatEmptyStateProps {
  displayName: string;
}

export function ChatEmptyState({ displayName }: ChatEmptyStateProps) {
  const firstName = displayName.split(" ")[0];

  return (
    <div className="flex-1 flex flex-col items-center justify-center px-4 py-8 sm:px-6 sm:py-12 select-none">
      <div className="max-w-lg w-full text-center">
        <div className="space-y-2">
          <h2 className="text-xl sm:text-2xl font-bold text-foreground">
            What would you like to work on, {firstName}?
          </h2>
          <p className="text-sm text-[var(--ui-text-secondary)]">
            Ask about business operations, files, or connected systems.
          </p>
        </div>
      </div>
    </div>
  );
}
