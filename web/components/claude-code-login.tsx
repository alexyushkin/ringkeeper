import { Button } from "@/components/ui";

/** The second way to use a subscription (desktop app): the Claude Code CLI's own
 * login on this computer, which expires sooner than a setup-token token. */
export function ClaudeCodeLogin({ busy, onClick }: { busy: boolean; onClick: () => void }) {
  return (
    <div className="space-y-1">
      <Button kind="ghost" busy={busy} onClick={onClick}>
        Or use my Claude Code login
      </Button>
      <p className="text-xs text-muted">
        If you use Claude Code on this computer, Ringkeeper can use its sign-in (not the Claude desktop app&apos;s). On a Mac, allow it when macOS asks about your Keychain. It expires sooner; Ringkeeper copies it again whenever Claude Code renews it.
      </p>
    </div>
  );
}
