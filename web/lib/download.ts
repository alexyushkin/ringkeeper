/**
 * Saving a file the page made (a table as CSV, a report). In a browser it's a
 * download link. The desktop app's window ignores those, so there the backend
 * saves it into the Downloads folder (POST desktop/downloads) and the page says
 * "Saved" with Show in folder.
 */

export type Save =
  | { how: "link"; name: string; href: string } // a browser: <a href download>
  | { how: "backend"; name: string; text: string }; // the desktop app: POST desktop/downloads

const UTF8_MARK = String.fromCharCode(0xfeff);

/** CSV that starts with the mark telling Excel it's UTF-8 (without it, accents break). */
export function forExcel(csv: string): string {
  return csv.startsWith(UTF8_MARK) ? csv : UTF8_MARK + csv;
}

export function dataUrl(text: string, type: string): string {
  return `data:${type};charset=utf-8,${encodeURIComponent(text)}`;
}

/** How to save it: a link in a browser, the backend in the desktop app. */
export function saveVia(desktop: boolean, name: string, text: string, type: string): Save {
  return desktop ? { how: "backend", name, text } : { how: "link", name, href: dataUrl(text, type) };
}

/** A saved report's name: "Check my setup 2026-11-11.md", or its table's ".csv". */
export function reportName(title: string, day: string, kind: "md" | "csv"): string {
  return `${title} ${day}${kind === "csv" ? " table" : ""}.${kind}`;
}

/** The file's own name, from where it was saved (Windows or not). */
export function fileName(path: string): string {
  return path.split(/[\\/]/).filter(Boolean).pop() ?? path;
}
