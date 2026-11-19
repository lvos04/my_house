import { createHash, generateKeyPairSync, sign } from 'node:crypto';
import { ADMIN, Api, expect, test, unique, VIEWER } from './helpers';
import { axe, contentOutside, focusStop, outsideCards, settled, smallTargets, smallText, widest } from './peil-metingen';

const title = 'Koppeling met de Hub (bediening)';
const path = '/system/hub-keys';

function testKey(kid: string) {
  const { privateKey, publicKey } = generateKeyPairSync('ed25519');
  const { crv, kty, x } = publicKey.export({ format: 'jwk' });
  const jwk = { crv, kty, x, kid };
  const thumbprint = createHash('sha256').update(JSON.stringify({ crv, kty, x })).digest('base64url');
  return { jwk, thumbprint, privateKey };
}

function fingerprint(jwt: string, policy: string[]) {
  const digest = createHash('sha256').update(`jl-hub-keyset-v1\njwt:${jwt}\npolicy:${policy.sort().join(',')}`).digest();
  const bits = [...digest.subarray(0, 10)].map((byte) => byte.toString(2).padStart(8, '0')).join('');
  const alphabet = '0123456789ABCDEFGHJKMNPQRSTVWXYZ';
  return bits.match(/.{5}/g)!.map((group) => alphabet[parseInt(group, 2)]).join('').match(/.{4}/g)!.join('-');
}

test('beheerder vergelijkt, pint en roteert de sleutelset via Systeem', async ({ adminPage: page, api }) => {
  const jwt = testKey('jwt');
  const policy = testKey('policy');
  const initial = fingerprint(jwt.thumbprint, [policy.thumbprint]);
  const source = JSON.stringify({ jwks: { keys: [jwt.jwk] }, policy_public_key: policy.jwk });
  await page.goto('/systeem');
  const card = page.getByRole('region', { name: title });
  const sourceField = card.getByLabel(/Publieke sleutelset/);
  const copyField = card.getByLabel(/Vingerafdruk uit de Hub/);
  expect(await api.get(path)).toEqual({ state: 'empty' });

  await sourceField.fill(source);
  await sourceField.press('Tab');
  const check = card.getByRole('button', { name: 'Controleren' });
  await expect(check).toBeFocused();
  await check.press('Enter');
  await expect(card.getByText(initial, { exact: true })).toBeVisible();
  await expect(copyField).toBeFocused();

  await copyField.fill('0000');
  await copyField.press('Enter');
  await expect(copyField).toHaveAttribute('aria-invalid', 'true');
  await expect(copyField).toHaveAccessibleDescription(/vingerafdruk komt niet overeen/);
  expect(await api.get(path)).toEqual({ state: 'empty' });

  await copyField.fill(initial.toLowerCase().replaceAll('-', ' ').replaceAll('0', 'o').replaceAll('1', 'l'));
  await copyField.press('Enter');
  await expect(card.getByText('Vastgelegde vingerafdruk', { exact: true })).toBeVisible();
  await expect(sourceField).toHaveCount(0);
  await card.getByRole('button', { name: 'Opnieuw koppelen' }).click();
  await expect(sourceField).toHaveValue('');
  await card.getByRole('button', { name: 'Annuleren' }).click();
  expect(await api.get(path)).toEqual({ state: 'pinned', fingerprint: initial, sequence: '0' });

  const nextPolicy = testKey('policy-nieuw');
  const next = fingerprint(jwt.thumbprint, [policy.thumbprint, nextPolicy.thumbprint]);
  const jws = [
    { alg: 'EdDSA', kid: 'policy', typ: 'hub-key-rotation+jws' },
    {
      type: 'hub_key_rotation', iss: 'hub', sequence: 1, issued_at: new Date().toISOString().replace(/\.\d{3}Z$/, 'Z'),
      purpose: 'policy', add: [nextPolicy.jwk], retire: [],
    },
  ].map((part) => Buffer.from(JSON.stringify(part)).toString('base64url')).join('.');
  const statement = jws + '.' + sign(null, Buffer.from(jws), policy.privateKey).toString('base64url');

  await card.getByRole('button', { name: 'Sleutelrotatie controleren' }).click();
  const rotation = card.getByLabel(/Ondertekende rotatieverklaringen/);
  await expect(rotation).toBeFocused();
  await rotation.fill(JSON.stringify({ items: [{ sequence: 1, statement }] }));
  await check.click();
  await expect(card.getByText('Rotatie wacht op bevestiging.')).toBeVisible();
  await expect(card.getByText(initial, { exact: true })).toBeVisible();
  await expect(card.getByText(next, { exact: true })).toBeVisible();
  expect((await api.get(path)).fingerprint).toBe(initial);

  await copyField.fill(next);
  await card.getByRole('button', { name: 'Vastleggen' }).click();
  await expect(rotation).toHaveCount(0);
  await page.reload();
  await expect(card.getByText(next, { exact: true })).toBeVisible();
  expect(await api.get(path)).toEqual({ state: 'pinned', fingerprint: next, sequence: '1' });
});

test('kijker ziet de Hub-koppeling nergens op Systeem', async ({ api, browser, baseURL }) => {
  await api.login(ADMIN);
  const username = 'hub-kijker-' + unique();
  await api.send('POST', '/users', { username, password: VIEWER.password, role: 'viewer' });
  const context = await browser.newContext({ baseURL });
  try {
    const viewer = new Api(context);
    await viewer.login({ username, password: VIEWER.password });
    const page = await context.newPage();
    await page.goto('/systeem');
    await expect(page.getByText('Instellingen, back-ups en herstellen zijn alleen beschikbaar voor beheerders.')).toBeVisible();
    await expect(page.getByRole('region', { name: title })).toHaveCount(0);
    expect((await context.request.get('/api/v1' + path)).status()).toBe(403);
  } finally {
    await context.close();
    const users = await api.get<{ id: string; username: string }[]>('/users');
    const account = users.find((user) => user.username === username);
    if (account) await api.send('DELETE', '/users/' + account.id);
  }
});

test('de open Hub-kaart sluit na rolverlies zonder herladen', async ({ api, browser, baseURL }) => {
  await api.login(ADMIN);
  const username = 'hub-rol-' + unique();
  const account = await api.send<{ id: string }>('POST', '/users', {
    username, password: VIEWER.password, role: 'admin',
  });
  const context = await browser.newContext({ baseURL });
  try {
    const other = new Api(context);
    await other.login({ username, password: VIEWER.password });
    const jwt = testKey('jwt');
    const policy = testKey('policy');
    const source = JSON.stringify({ jwks: { keys: [jwt.jwk] }, policy_public_key: policy.jwk });
    const page = await context.newPage();
    await page.goto('/systeem');
    const card = page.getByRole('region', { name: title });
    if ((await other.get(path)).state === 'pinned') {
      await card.getByRole('button', { name: 'Opnieuw koppelen' }).click();
    }
    const sourceField = card.getByLabel(/Publieke sleutelset/);
    await expect(sourceField).toHaveValue('');
    await sourceField.fill(source);
    const before = await api.get(path);
    await api.send('PATCH', '/users/' + account.id, { role: 'viewer' });
    expect((await other.get('/auth/me')).role).toBe('viewer');

    const response = page.waitForResponse(r => r.url().endsWith(path + '/verify'));
    await card.getByRole('button', { name: 'Controleren' }).click();
    expect((await response).status()).toBe(403);
    await expect(card).toHaveCount(0);
    await expect(sourceField).toHaveCount(0);
    await expect(page.getByText('Instellingen, back-ups en herstellen zijn alleen beschikbaar voor beheerders.')).toBeVisible();
    expect(await api.get(path)).toEqual(before);
  } finally {
    await context.close();
    await api.send('DELETE', '/users/' + account.id);
  }
});

test('Hub-koppeling in Peil op zes breedtes in licht en donker', async ({ adminPage: page, api }) => {
  const jwt = testKey('jwt');
  const policy = testKey('policy');
  await page.goto('/systeem');
  const card = page.getByRole('region', { name: title });
  if ((await api.get(path)).state === 'pinned') {
    await card.getByRole('button', { name: 'Opnieuw koppelen' }).click();
  }
  await card.getByLabel(/Publieke sleutelset/).fill(JSON.stringify({ jwks: { keys: [jwt.jwk] }, policy_public_key: policy.jwk }));
  await card.getByRole('button', { name: 'Controleren' }).click();
  const field = card.getByLabel(/Vingerafdruk uit de Hub/);
  await expect(field).toBeFocused();
  await field.fill(fingerprint(jwt.thumbprint, [policy.thumbprint]));
  for (const scheme of ['light', 'dark'] as const) {
    await page.emulateMedia({ colorScheme: scheme });
    for (const width of [320, 360, 390, 768, 1024, 1440]) {
      const label = `Hub-koppeling ${scheme}, ${width} px`;
      await page.setViewportSize({ width, height: 850 });
      await settled(page);
      await expect(page.locator('html')).toHaveAttribute('data-theme', scheme);
      await axe(page, label);
      expect(await page.evaluate(() => document.documentElement.scrollWidth), `${label} · ${await widest(page)}`).toBeLessThanOrEqual(width);
      expect(await smallTargets(page), label).toEqual([]);
      expect(await smallText(page), label).toEqual([]);
      expect(await outsideCards(page), label).toEqual([]);
      expect(await contentOutside(page), label).toEqual([]);
      await field.press('Tab');
      await expect(card.getByRole('button', { name: 'Vastleggen' })).toBeFocused();
      const ring = await focusStop(page);
      expect(ring?.ring, label).toBeGreaterThanOrEqual(3);
      expect(ring?.contrast, label).toBeGreaterThanOrEqual(3);
      expect(ring?.bedekt, label).toBe(false);
      await field.focus();
    }
  }
});
