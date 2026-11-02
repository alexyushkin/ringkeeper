/** The user guide, on GitHub: docs/<page>.md in the main repository. */
export const DOCS = "https://github.com/alexyushkin/ringkeeper/blob/main/docs";

export function docUrl(page: string): string {
  return `${DOCS}/${page}.md`;
}
