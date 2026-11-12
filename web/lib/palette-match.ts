/** How the Ctrl+K search ranks what it lists (components/command-palette.tsx). */

/** How well `query` matches `text`: its letters in order, closer together and at
 * word starts scoring higher; null when they aren't all there. */
export function fuzzy(query: string, text: string): number | null {
  const q = query.toLowerCase().replace(/\s+/g, "");
  if (!q) return 0;
  const t = text.toLowerCase();
  let score = 0;
  let at = -1;
  for (const ch of q) {
    const next = t.indexOf(ch, at + 1);
    if (next < 0) return null;
    score += next === at + 1 ? 3 : 0; // runs of letters
    score += next === 0 || /[\s›/_-]/.test(t[next - 1] ?? "") ? 2 : 0; // word starts
    score -= Math.min(next - at - 1, 5) * 0.1; // gaps
    at = next;
  }
  return t.includes(query.toLowerCase().trim()) ? score + 10 : score;
}

/** Every word of `query` starts one of `words` ("back" finds "backup"): the plain words a
 * Settings section is also found by. Whole words only, not scattered letters. */
export function wordsHit(query: string, words: string): boolean {
  const wanted = query.toLowerCase().split(/\s+/).filter(Boolean);
  if (!wanted.length) return false;
  const have = words.toLowerCase().split(/[\s,]+/).filter(Boolean);
  return wanted.every((w) => have.some((h) => h.startsWith(w)));
}

/** A label that matches outranks a match on its plain words alone. */
const BY_WORDS = 8;

/** Its score for `query`, or null when it doesn't match. An action that starts something
 * ("Run Check my setup") comes after the place it opens unless the query says "run": a
 * search for "my setup" lands on the section. */
export function score(query: string, label: string, hint = "", words = ""): number | null {
  const byLabel = fuzzy(query, `${label} ${hint}`);
  const byWords = words && wordsHit(query, `${label} ${words}`) ? BY_WORDS : null;
  const best = byLabel === null ? byWords : byWords === null ? byLabel : Math.max(byLabel, byWords);
  const startsSomething = label.toLowerCase().startsWith("run ");
  const saysRun = query.toLowerCase().split(/\s+/).includes("run");
  return best === null || !startsSomething || saysRun ? best : best - 2;
}
