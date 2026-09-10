/**
 * crypto.randomUUID() only works in a "secure context" (HTTPS or
 * localhost/127.0.0.1). OpenJarvis is commonly reached over plain HTTP on a
 * LAN or Tailscale IP (e.g. http://100.x.y.z:8010), which browsers treat as
 * insecure — crypto.randomUUID is simply undefined there, which crashes the
 * app before React ever mounts (TypeError: crypto.randomUUID is not a
 * function).
 *
 * This falls back to crypto.getRandomValues() (available in every context,
 * secure or not) to build a spec-compliant v4 UUID, and only as an absolute
 * last resort to Math.random().
 */
export function randomUUID(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }

  if (typeof crypto !== 'undefined' && typeof crypto.getRandomValues === 'function') {
    const bytes = crypto.getRandomValues(new Uint8Array(16));
    bytes[6] = (bytes[6] & 0x0f) | 0x40; // version 4
    bytes[8] = (bytes[8] & 0x3f) | 0x80; // variant 10
    const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, '0'));
    return (
      `${hex.slice(0, 4).join('')}-${hex.slice(4, 6).join('')}-` +
      `${hex.slice(6, 8).join('')}-${hex.slice(8, 10).join('')}-` +
      `${hex.slice(10, 16).join('')}`
    );
  }

  // Not cryptographically strong, but this path only runs when the
  // platform exposes neither of the APIs above.
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    const v = c === 'x' ? r : (r & 0x3) | 0x8;
    return v.toString(16);
  });
}
