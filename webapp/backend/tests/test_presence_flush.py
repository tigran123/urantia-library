"""Regression test for the presence-flush background loop.

`background._last_seen_flush_loop` runs as a fire-and-forget asyncio task
created in the ASGI lifespan. The existing lifespan tests enter
`with TestClient(main.app)` but the loop's *body* never gets scheduled before
shutdown cancels it, so an unresolved name inside the loop (e.g. a module
constant that wasn't imported after the main.py → foundation-module split) slips
through every gate — `import main` succeeds, the route table is unchanged, and
pytest is green, yet the running server's flush loop dies on its first tick.

This test runs the loop directly with a near-zero interval so its body executes,
turning that class of latent NameError into a loud failure.
"""
import asyncio


def test_last_seen_flush_loop_body_runs(app_ctx, monkeypatch):
    helpers, _emails, _TestSession = app_ctx
    import background

    # Near-zero interval so the loop ticks immediately; stub the DB flush so the
    # test doesn't depend on real session I/O (it only proves the loop body's
    # names resolve and it keeps looping).
    monkeypatch.setattr(background, "_LAST_SEEN_FLUSH_INTERVAL_SECONDS", 0.001, raising=False)
    ticks = []
    monkeypatch.setattr(background, "_flush_last_seen_once", lambda: ticks.append(1))

    async def _run_briefly():
        task = asyncio.create_task(background._last_seen_flush_loop())
        await asyncio.sleep(0.05)  # let it tick several times
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        # If the loop crashed (e.g. NameError on iteration 1) rather than being
        # cancelled mid-sleep, the await above re-raises that exception, failing
        # the test loudly — which is exactly what we want.

    asyncio.run(_run_briefly())
    assert ticks, "flush loop never reached _flush_last_seen_once — an unresolved name likely killed iteration 1"
