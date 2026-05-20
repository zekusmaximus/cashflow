// Port of server/src/liquidity_gate_mcp/tools.py scoring logic.
// Two implementations exist (Python for the MCP server, TS for the desktop
// intake view) because the desktop app fetches the file list synchronously
// rather than going through the stdio MCP transport. Keep this aligned with
// the Python tokenize/score_candidate functions if you touch either.

export const MATCH_THRESHOLD = 0.35;

const STOP_WORDS = new Set([
  'all',
  'and',
  'annual',
  'export',
  'history',
  'if',
  'in',
  'of',
  'or',
  'pdf',
  'screenshot',
  'the',
  'ytd',
  '2026',
  '2027',
]);

const CAMEL_BOUNDARY = /(?<=[a-z])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])/g;

export function tokenize(value: string): Set<string> {
  const spaced = value.replace(CAMEL_BOUNDARY, ' ');
  const tokens = new Set<string>();
  const chunks = spaced.toLowerCase().match(/[a-z0-9]+/g) ?? [];
  for (const chunk of chunks) {
    const parts = chunk.match(/[a-z]+|[0-9]+/g) ?? [];
    for (const part of parts) {
      if (part.length > 2 && !STOP_WORDS.has(part)) {
        tokens.add(part);
      }
    }
  }
  return tokens;
}

// Dice coefficient over character bigrams. Approximates Python's
// SequenceMatcher.ratio closely enough for our threshold; exact parity with
// Ratcliff-Obershelp isn't required since the threshold is calibrated to this
// scoring shape via the same test set.
function bigramRatio(a: string, b: string): number {
  if (a === b) return a.length === 0 ? 0 : 1;
  if (a.length < 2 || b.length < 2) return 0;
  const aBigrams = new Set<string>();
  for (let i = 0; i < a.length - 1; i += 1) aBigrams.add(a.slice(i, i + 2));
  const bBigrams = new Set<string>();
  for (let i = 0; i < b.length - 1; i += 1) bBigrams.add(b.slice(i, i + 2));
  let intersection = 0;
  for (const bg of aBigrams) if (bBigrams.has(bg)) intersection += 1;
  return (2 * intersection) / (aBigrams.size + bBigrams.size);
}

function normalizeText(value: string): string {
  return [...tokenize(value)].sort().join(' ');
}

function expectedExtensions(format: string): Set<string> {
  const normalized = format.toLowerCase();
  const exts = new Set<string>();
  if (normalized.includes('csv')) exts.add('csv');
  if (normalized.includes('pdf')) exts.add('pdf');
  if (normalized.includes('screenshot')) {
    exts.add('png');
    exts.add('jpg');
    exts.add('jpeg');
    exts.add('webp');
  }
  if (normalized.includes('worksheet')) {
    exts.add('csv');
    exts.add('xlsx');
  }
  if (normalized.includes('invoice')) {
    exts.add('pdf');
    exts.add('csv');
  }
  return exts;
}

export interface ScoringRow {
  document: string;
  category: string;
  subjectMatter: string;
  whyNeeded: string;
  format: string;
}

export interface CandidateFile {
  relativePath: string;
  filename: string;
  stem: string;
  extension: string;
  parentName: string;
  /** Epoch milliseconds of last modification; absent when the indexer can't read it. */
  modifiedMs?: number;
}

export interface ScoredMatch {
  candidate: CandidateFile;
  score: number;
}

export function scoreCandidate(row: ScoringRow, candidate: CandidateFile): number {
  const haystack = [row.document, row.category, row.subjectMatter, row.whyNeeded].join(' ');
  const candidateText = [candidate.stem, candidate.parentName, candidate.extension].join(' ');
  const docTokens = tokenize(haystack);
  const candidateTokens = tokenize(candidateText);
  let intersection = 0;
  for (const t of docTokens) if (candidateTokens.has(t)) intersection += 1;
  const overlap = intersection / Math.max(docTokens.size, 1);
  const ratio = bigramRatio(normalizeText(row.document), normalizeText(candidate.stem));
  const extensionBonus = expectedExtensions(row.format).has(candidate.extension) ? 0.15 : 0;
  return overlap * 0.65 + ratio * 0.35 + extensionBonus;
}

export function findMatches(row: ScoringRow, candidates: CandidateFile[]): ScoredMatch[] {
  return candidates
    .map((candidate) => ({ candidate, score: scoreCandidate(row, candidate) }))
    .filter((scored) => scored.score >= MATCH_THRESHOLD)
    .sort((a, b) => b.score - a.score)
    .slice(0, 3);
}
