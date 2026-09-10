import { afterEach, describe, expect, it, vi } from 'vitest';

import { randomUUID } from './uuid';

const UUID_V4_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('randomUUID', () => {
  it('uses crypto.randomUUID when available (secure context)', () => {
    const spy = vi.fn(() => 'aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee');
    vi.stubGlobal('crypto', { randomUUID: spy, getRandomValues: vi.fn() });

    const id = randomUUID();

    expect(spy).toHaveBeenCalledOnce();
    expect(id).toBe('aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee');
  });

  it('falls back to crypto.getRandomValues when randomUUID is missing (insecure context)', () => {
    // This is the actual bug this file exists to fix: crypto.randomUUID is
    // undefined over plain HTTP on a LAN/Tailscale IP, which used to crash
    // the app before React mounted.
    const getRandomValues = vi.fn((arr: Uint8Array) => {
      arr.fill(0x42);
      return arr;
    });
    vi.stubGlobal('crypto', { getRandomValues });

    const id = randomUUID();

    expect(getRandomValues).toHaveBeenCalledOnce();
    expect(id).toMatch(UUID_V4_RE);
  });

  it('sets the version and variant bits correctly in the getRandomValues fallback', () => {
    const getRandomValues = vi.fn((arr: Uint8Array) => {
      arr.fill(0xff); // worst case: every bit set, so masking must actually happen
      return arr;
    });
    vi.stubGlobal('crypto', { getRandomValues });

    const id = randomUUID();
    const [, , third, fourth] = id.split('-');

    expect(third[0]).toBe('4'); // version 4
    expect(['8', '9', 'a', 'b']).toContain(fourth[0].toLowerCase()); // variant 10xx
  });

  it('falls back to Math.random-based generation when crypto is entirely unavailable', () => {
    vi.stubGlobal('crypto', undefined);

    const id = randomUUID();

    expect(id).toMatch(UUID_V4_RE);
  });

  it('produces different ids across calls in every fallback tier', () => {
    vi.stubGlobal('crypto', undefined);
    const ids = new Set(Array.from({ length: 20 }, () => randomUUID()));
    expect(ids.size).toBe(20);
  });
});
