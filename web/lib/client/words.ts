/* Plain-language labels and small helpers shared by the screens. */

export function slugify(text: string): string {
  return text
    .toLowerCase()
    .normalize("NFKD")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 63)
    .replace(/-+$/, "");
}

export const AUTONOMY: Record<string, [string, string]> = {
  strict: ["Careful", "Asks before most changes. Good for sensitive clients."],
  standard: ["Normal", "Works on its own, asks before changing live (production) data."],
  relaxed: ["Hands-off", "Fewer questions. Production changes are still held for your approval."],
};

export const SERVICE: Record<string, string> = {
  supabase: "Database",
  n8n: "n8n (automations)",
  neon: "Neon",
  vercel: "Vercel",
  airtable: "Airtable",
  google: "Google",
  twilio: "Twilio",
  gmail: "Gmail",
  mcp: "MCP server",
};

export const ENVS: [string, string][] = [
  ["prod", "Production (live client data)"],
  ["staging", "Staging"],
  ["dev", "Test"],
];

export const KIND: Record<string, string> = {
  approve: "Live change",
  plan: "Plan",
  question: "Question",
  route: "Which project?",
};

export function download(filename: string, text: string): void {
  const url = URL.createObjectURL(new Blob([text], { type: "text/plain" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}
