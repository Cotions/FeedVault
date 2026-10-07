// Worst cases for the layout checks (layout.spec.js), on top of what
// make_demo.py --stress writes: through the throwaway instance's own API
// (with its X-FeedVault header), never another server. STRESSpost01 gets
// 15 tags, long ones among them, and goes in a collection with a long name;
// its creator gets a person with a long name, and saved links with a long
// title, address and notes (on the Links page and theirs); STRESSpost02 is
// trashed, so the Trash page has a post in it.
//
// Resolves to what the specs open: { post, author, collection, person }.
export const STRESS_POST = { platform: "instagram", post_id: "STRESSpost01", id: "instagram:STRESSpost01" };
export const STRESS_TRASHED = "instagram:STRESSpost02";
const MAX_NAME = 64;                                  // organize.MAX_NAME: tag, collection and person names

export const STRESS_TAGS = [
  "an-extremely-long-tag-name-that-goes-on-and-on-to-the-limit-of64",
  "another very long tag with spaces, that has to wrap somewhere ok",
  "travel-photography-from-the-old-archive",
  "ceramics", "favourites", "a", "x", "to-review-later", "night", "film",
  "trams-and-other-public-transport-at-night", "2023", "garden", "macro",
  "UnbrokenUnbrokenUnbrokenUnbrokenUnbrokenUnbrokenUnbrokenUnbroken",
];
export const STRESS_COLLECTION = "A collection with a very long name, to see where its title wraps";
export const STRESS_PERSON = "Someone With A Name Long Enough To Push Every Header To The Edge";

async function call(base, method, url, body) {
  const r = await fetch(base + url, {
    method,
    headers: { "X-FeedVault": "1", ...(body ? { "Content-Type": "application/json" } : {}) },
    body: body ? JSON.stringify(body) : undefined,
    signal: AbortSignal.timeout(10_000),
  });
  const out = await r.json().catch(() => null);
  if (!r.ok || out?.ok === false) throw new Error(`e2e stress: ${method} ${url}: HTTP ${r.status} ${out?.error || ""}`);
  return out;
}

// Saved links of the stress person: the longest of each field. Invented addresses, never opened.
export const STRESS_LINKS = [
  { url: "https://www.patreon.com/" + "averyveryverylongcreatorname".repeat(4),
    title: "A link title long enough to need cutting short somewhere in the row, ".repeat(3).trim() },
  { url: "https://interviews.example/" + "an-unbroken-path-segment-".repeat(14) + "?ref=" + "x".repeat(80),
    title: "", notes: "Notes over several lines.\n" + "Words that go on and on about the interview. ".repeat(12) },
];

export async function seedStress(base) {
  for (const n of [...STRESS_TAGS, STRESS_COLLECTION, STRESS_PERSON]) {
    if (n.length > MAX_NAME) throw new Error(`e2e stress: name longer than ${MAX_NAME}: ${n}`);
  }
  const post = await call(base, "GET", `/api/posts/${STRESS_POST.platform}/${STRESS_POST.post_id}`);
  if (post.author?.handle?.length !== 60 || post.author?.name?.length !== 60) {
    throw new Error(`e2e stress: the demo was not made with --stress (creator ${post.author?.handle})`);
  }
  await call(base, "POST", "/api/tags/apply", { posts: [STRESS_POST.id], add: STRESS_TAGS });
  const { collection } = await call(base, "POST", "/api/collections", { name: STRESS_COLLECTION });
  const others = (await call(base, "GET", "/api/posts?limit=3")).posts.map(p => p.id);
  await call(base, "POST", `/api/collections/${collection.id}/add`, { posts: [STRESS_POST.id, ...others] });
  const { person } = await call(base, "POST", "/api/people",
    { name: STRESS_PERSON, accounts: [{ platform: STRESS_POST.platform, id: post.author.id }] });
  for (const l of STRESS_LINKS) await call(base, "POST", "/api/links", { ...l, person: person.id });
  const trashed = await call(base, "POST", "/api/delete", { posts: [STRESS_TRASHED] });
  if (!trashed.posts?.includes(STRESS_TRASHED)) throw new Error(`e2e stress: ${STRESS_TRASHED} was not trashed`);
  return { post: STRESS_POST, author: post.author, collection: collection.id, person: person.id };
}
