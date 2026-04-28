const KEY = "atlas.session_ids.v1";

function readMap(): Record<string, string> {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw);
    return typeof parsed === "object" && parsed !== null ? parsed : {};
  } catch {
    return {};
  }
}

function writeMap(map: Record<string, string>): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(map));
  } catch {
    // Quota or privacy mode — degrade silently; the user gets a non-persistent session.
  }
}

export function getOrCreateSessionId(project_id: string): string {
  const map = readMap();
  if (map[project_id]) return map[project_id];
  const id = crypto.randomUUID();
  writeMap({ ...map, [project_id]: id });
  return id;
}

export function clearSessionId(project_id: string): void {
  const map = readMap();
  delete map[project_id];
  writeMap(map);
}
