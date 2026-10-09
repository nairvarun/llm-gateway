"""The admin API and its three layers of protection."""

from __future__ import annotations

from tests.conftest import ADMIN_TOKEN
from tests.fakes.upstream import chunks, usage


async def test_virtual_key_on_admin_is_403(gateway):
    key = await gateway.create_key()
    h = {"Authorization": f"Bearer {key}"}
    assert (
        await gateway.client.post("/admin/keys", json={"name": "x"}, headers=h)
    ).status_code == 403
    assert (await gateway.client.get("/admin/keys/key_x/usage", headers=h)).status_code == 403
    assert (await gateway.client.delete("/admin/keys/key_x", headers=h)).status_code == 403


async def test_admin_requires_correct_token(gateway):
    assert (await gateway.client.post("/admin/keys", json={"name": "x"})).status_code == 401
    bad = {"Authorization": "Bearer " + "y" * 40}
    r = await gateway.client.post("/admin/keys", json={"name": "x"}, headers=bad)
    assert r.status_code == 401


async def test_create_key_validates_models(gateway):
    r = await gateway.client.post(
        "/admin/keys", json={"name": "x", "allowed_models": ["nope"]}, headers=gateway.admin
    )
    assert r.status_code == 400


async def test_key_is_stored_hashed(gateway):
    r = await gateway.client.post("/admin/keys", json={"name": "x"}, headers=gateway.admin)
    body = r.json()
    assert body["key"].startswith("gw-") and body["id"].startswith("key_")
    async with gateway.deps.store.db.execute("SELECT hash FROM api_keys") as cur:
        (stored,) = await cur.fetchone()
    assert stored != body["key"] and len(stored) == 64


async def test_usage_summary(gateway, upstream):
    key = await gateway.create_key()
    upstream.script(chunks(1), usage(100, 10))
    upstream.script(chunks(1), usage(50, 5))
    await gateway.chat(key, model="gpt")
    await gateway.chat(key, model="claude")
    async with gateway.deps.store.db.execute("SELECT id FROM api_keys") as cur:
        (kid,) = await cur.fetchone()
    r = await gateway.client.get(f"/admin/keys/{kid}/usage", headers=gateway.admin)
    s = r.json()
    assert s["requests"] == 2
    assert (s["input_tokens"], s["output_tokens"]) == (150, 15)
    assert [a["alias"] for a in s["by_alias"]] == ["claude", "gpt"]


async def test_unknown_key_404(gateway):
    h = {"Authorization": f"Bearer {ADMIN_TOKEN}"}
    assert (await gateway.client.get("/admin/keys/key_nope/usage", headers=h)).status_code == 404
    assert (await gateway.client.delete("/admin/keys/key_nope", headers=h)).status_code == 404
