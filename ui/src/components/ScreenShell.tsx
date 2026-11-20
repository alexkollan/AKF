import type { ReactNode } from "react";

interface ScreenShellProps {
  title: string;
  description: string;
  milestone: string;
  children?: ReactNode;
}

/**
 * Frame every screen shares: title, one-line purpose and the body.
 *
 * While a screen is still a placeholder it names the milestone that fills it in, so the
 * shell is honest about what works instead of showing empty controls.
 */
export function ScreenShell({ title, description, milestone, children }: ScreenShellProps) {
  return (
    <section className="flex h-full flex-col gap-4 overflow-y-auto p-6">
      <header className="flex flex-col gap-1">
        <h1 className="text-xl font-semibold tracking-tight">{title}</h1>
        <p className="text-sm text-muted-foreground">{description}</p>
      </header>
      {children ?? (
        <div className="rounded-lg border border-dashed border-border bg-card/40 p-6 text-sm text-muted-foreground">
          Built in {milestone}.
        </div>
      )}
    </section>
  );
}
