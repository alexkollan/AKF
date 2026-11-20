import type { ReactNode } from "react";

import { useProfile } from "@/lib/engine";

interface ScreenShellProps {
  title: string;
  description: string;
  children: ReactNode;
  /** Right of the title: buttons that act on the whole screen. */
  actions?: ReactNode;
}

/**
 * Frame every settings screen shares: title, one line of purpose, a scrolling column.
 * Until the engine has sent its snapshot there is nothing to edit, so it says so.
 */
export function ScreenShell({ title, description, children, actions }: ScreenShellProps) {
  const profile = useProfile();
  return (
    <section className="flex h-full flex-col overflow-y-auto">
      <header className="sticky top-0 z-10 flex items-end gap-4 border-b border-shoal/70 bg-abyss/95 px-8 pt-7 pb-4 backdrop-blur">
        <div className="min-w-0">
          <h1 className="text-2xl font-semibold tracking-tight [font-stretch:112%]">{title}</h1>
          <p className="mt-1 max-w-prose text-sm text-mist">{description}</p>
        </div>
        {actions && <div className="ml-auto flex shrink-0 gap-2">{actions}</div>}
      </header>
      <div className="flex max-w-4xl flex-col gap-7 px-8 py-6">
        {profile ? children : <p className="text-sm text-mist">Waiting for the engine…</p>}
      </div>
    </section>
  );
}
